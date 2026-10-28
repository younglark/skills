#!/bin/sh
# grokbot-proto-restore -- rebuild the .proto files and typed clients from a
# (possibly new) Grok Bot.app, then prove the result is byte-exact.
#
# Usage
#   restore.sh                                  # /Applications/Grok Bot.app -> ./grokbot-proto
#   restore.sh /path/to/Grok\ Bot.app ./out
#   restore.sh --proto-cjs /path/to/proto.cjs ./out   # already unpacked
#   restore.sh --baseline ./old/proto ...        # also diff against a previous run
#   restore.sh --no-codegen ...                  # only .proto + protoc check
#
# Stages: unpack -> locate proto.cjs -> read pinned dep versions -> extract
#         -> protoc check -> (codegen) -> descriptor + wire cross-check -> report
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

APP="/Applications/Grok Bot.app"
OUT=""
PROTO_CJS=""
BASELINE=""
CODEGEN=1
DIAG=0

while [ $# -gt 0 ]; do
  case "$1" in
    --proto-cjs) PROTO_CJS="$2"; shift 2;;
    --baseline)  BASELINE="$2"; shift 2;;
    --no-codegen) CODEGEN=0; shift;;
    --diagnose)  DIAG=1; shift;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0;;
    *) if [ -z "$OUT" ]; then
         case "$1" in
           *.app|*.app/) APP="$1";;
           *) OUT="$1";;
         esac
       else OUT="$1"; fi
       shift;;
  esac
done
[ -n "$OUT" ] || OUT="$PWD/grokbot-proto"

log() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
log "output: $OUT"

# ---------------------------------------------------------------- 0. preflight
command -v python3 >/dev/null || die "python3 missing"
command -v node    >/dev/null || die "node missing"
python3 -c "import grpc_tools" 2>/dev/null \
  || die "grpcio-tools missing (needed for a working protoc): pip install grpcio-tools"

# ---------------------------------------------------------------- 1. unpack
if [ -z "$PROTO_CJS" ]; then
  ASAR="$APP/Contents/Resources/app.asar"
  [ -f "$ASAR" ] || die "no app.asar under $APP (is the app installed?)"
  if [ ! -d "$OUT/app" ]; then
    log "unpacking $ASAR"
    npx --yes @electron/asar extract "$ASAR" "$OUT/app"
  fi
  PROTO_CJS="$(find "$OUT/app" -name proto.cjs -path '*electron-main*' -size +200k | head -1)"
  [ -n "$PROTO_CJS" ] || PROTO_CJS="$(find "$OUT/app" -name 'proto*.cjs' -size +200k | head -1)"
  [ -n "$PROTO_CJS" ] || die "could not locate proto.cjs inside $OUT/app"
fi
[ -f "$PROTO_CJS" ] || die "proto.cjs not found: $PROTO_CJS"
echo "proto.cjs: $PROTO_CJS"

APPVER=""
PLIST="$APP/Contents/Info.plist"
[ -f "$PLIST" ] && APPVER="$(defaults read "$PLIST" CFBundleShortVersionString 2>/dev/null || true)"
echo "app version: ${APPVER:-unknown}"

# ---------------------------------------------------------------- 2. pin versions
# The generators must match what the app actually ships, otherwise the emitted
# code shape (and enum value names) will not line up with proto.cjs.
APPDIR="$(dirname "$PROTO_CJS")"
read_dep() { python3 - "$1" "$2" <<'PY'
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: print(""); sys.exit()
for k in ("dependencies","devDependencies"):
    v=(d.get(k) or {}).get(sys.argv[2])
    if v: print(v.lstrip("^~=")); break
PY
}
PKGJSON="$(cd "$APPDIR/../.." && pwd)/package.json"
PROTOBUF_V="$(read_dep "$PKGJSON" @bufbuild/protobuf)"
CONNECT_V="$(read_dep "$PKGJSON" @connectrpc/connect)"
[ -n "$PROTOBUF_V" ] || PROTOBUF_V="1.10.1"
[ -n "$CONNECT_V" ]  || CONNECT_V="1.6.1"
echo "pinned: @bufbuild/protobuf=$PROTOBUF_V  @connectrpc/connect=$CONNECT_V"

case "$PROTOBUF_V" in
  1.*) : ;;
  *) echo "\033[33mWARNING: protobuf major version $PROTOBUF_V is not 1.x -- the"
     echo "         compact-descriptor format may have changed. Read"
     echo "         references/troubleshooting.md before trusting the output.\033[0m";;
esac

# ---------------------------------------------------------------- 3. extract
log "extracting .proto"
python3 "$HERE/extract_proto.py" "$PROTO_CJS" "$OUT/proto" > "$OUT/extract.log" \
  || die "extract_proto.py failed"
cat "$OUT/extract.log"
# acceptance gates: a non-zero value here means the reconstruction is wrong,
# not that the app changed -- see references/troubleshooting.md
grep -q "^unresolved refs: 0 " "$OUT/extract.log" \
  || die "unresolved refs != 0: the descriptor grammar changed, see references/descriptor-format.md"
grep -q "backward cross-file edges: 0" "$OUT/extract.log" \
  || die "backward cross-file edges != 0: import cycle survived segmentation"
grep -q "^types relocated to another package: 0" "$OUT/extract.log" \
  || die "types were relocated across packages: full names no longer match proto.cjs"

# ---------------------------------------------------------------- 4. protoc
log "protoc validation"
rm -f "$OUT/desc.pb"
( cd "$OUT/proto" && python3 -m grpc_tools.protoc -I. \
    --descriptor_set_out="$OUT/desc.pb" ./*.proto ) \
  || die "protoc failed -- see references/troubleshooting.md"
echo "descriptor: $(wc -c < "$OUT/desc.pb") bytes, 0 errors"

if [ "$DIAG" = 1 ]; then
  log "dependency-cycle diagnosis"
  python3 "$HERE/diagnose_cycles.py" "$PROTO_CJS"
fi

# ---------------------------------------------------------------- 5. codegen
if [ "$CODEGEN" = 1 ]; then
  log "generating typed clients"
  mkdir -p "$OUT/codegen/src"
  cat > "$OUT/codegen/package.json" <<EOF
{
  "name": "grokbot-codegen",
  "private": true,
  "type": "module",
  "devDependencies": {
    "@bufbuild/protoc-gen-es": "$PROTOBUF_V",
    "@connectrpc/protoc-gen-connect-es": "$CONNECT_V"
  },
  "dependencies": {
    "@bufbuild/protobuf": "$PROTOBUF_V",
    "@connectrpc/connect": "$CONNECT_V",
    "@connectrpc/connect-node": "$CONNECT_V"
  }
}
EOF
  ( cd "$OUT/codegen" && npm install --silent ) || die "npm install failed"
  rm -rf "$OUT/codegen/src/gen" "$OUT/codegen/src/js"
  mkdir -p "$OUT/codegen/src/gen" "$OUT/codegen/src/js"
  P="$OUT/codegen/node_modules/.bin"
  # TypeScript (editor / type checking)
  python3 -m grpc_tools.protoc -I "$OUT/proto" \
    --plugin=protoc-gen-es="$P/protoc-gen-es" \
    --es_out="$OUT/codegen/src/gen" --es_opt=target=ts \
    "$OUT"/proto/*.proto
  # JavaScript + typed service clients (runnable)
  python3 -m grpc_tools.protoc -I "$OUT/proto" \
    --plugin=protoc-gen-es="$P/protoc-gen-es" \
    --plugin=protoc-gen-connect-es="$P/protoc-gen-connect-es" \
    --es_out="$OUT/codegen/src/js" --es_opt=target=js \
    --connect-es_out="$OUT/codegen/src/js" --connect-es_opt=target=js \
    "$OUT"/proto/*.proto

  # ------------------------------------------------------------ 6. cross-check
  log "descriptor cross-check (proto.cjs vs generated)"
  cp "$HERE/verify/compare.mjs" "$HERE/verify/cross-wire.mjs" "$OUT/codegen/"
  ( cd "$OUT/codegen" && node compare.mjs "$PROTO_CJS" "$OUT/proto" ) \
    || die "descriptor mismatch -- do not ship these .proto files"

  log "wire cross-check (encode with app types, decode with generated types)"
  ( cd "$OUT/codegen" && node cross-wire.mjs "$PROTO_CJS" ) \
    || die "wire mismatch -- do not ship these .proto files"
fi

# ---------------------------------------------------------------- 7. baseline diff
if [ -n "$BASELINE" ] && [ -f "$BASELINE/snapshot.json" ]; then
  log "diff against baseline $BASELINE"
  python3 "$HERE/diff_snapshot.py" "$BASELINE/snapshot.json" "$OUT/proto/snapshot.json"
fi

log "done"
echo "  .proto      : $OUT/proto"
echo "  descriptor  : $OUT/desc.pb"
[ "$CODEGEN" = 1 ] && echo "  generated   : $OUT/codegen/src/{gen,js}"
echo "  snapshot    : $OUT/proto/snapshot.json"
echo "$APPVER" > "$OUT/APP_VERSION"
