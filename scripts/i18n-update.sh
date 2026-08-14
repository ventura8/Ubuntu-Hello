#!/usr/bin/env bash
# Refresh gettext templates, merge .po files, and assert LINGUAS ↔ Whisper list.
# Usage: ./scripts/i18n-update.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WHISPER_LIST="$ROOT/po/whisper-languages.txt"
CORE_PO="$ROOT/ubuntu-hello/po"
GTK_PO="$ROOT/ubuntu-hello-gtk/po"

die() { echo "i18n-update: $*" >&2; exit 1; }

[[ -f "$WHISPER_LIST" ]] || die "missing $WHISPER_LIST"

assert_linguas() {
	local linguas="$1"
	[[ -f "$linguas" ]] || die "missing $linguas"
	if ! diff -u "$WHISPER_LIST" "$linguas" >/dev/null; then
		echo "i18n-update: LINGUAS out of sync with po/whisper-languages.txt:" >&2
		diff -u "$WHISPER_LIST" "$linguas" >&2 || true
		die "sync LINGUAS from po/whisper-languages.txt (omit en)"
	fi
}

sync_linguas_from_whisper() {
	# Optional helper: copy Whisper list into both LINGUAS files.
	cp "$WHISPER_LIST" "$CORE_PO/LINGUAS"
	cp "$WHISPER_LIST" "$GTK_PO/LINGUAS"
}

if [[ "${1:-}" == "--sync-linguas" ]]; then
	sync_linguas_from_whisper
	echo "Synced LINGUAS from po/whisper-languages.txt"
fi

assert_linguas "$CORE_PO/LINGUAS"
assert_linguas "$GTK_PO/LINGUAS"

UH_VERSION="$(python3 "$ROOT/scripts/read-version.py")"

echo "Extracting ubuntu-hello.pot (version ${UH_VERSION})..."
xgettext --files-from="$CORE_PO/POTFILES" --directory="$ROOT/ubuntu-hello" \
	--from-code=UTF-8 \
	--keyword=S:1 --keyword=_:1 --keyword=N_ --keyword=ngettext:1,2 \
	--add-comments=TRANSLATORS \
	--package-name=ubuntu-hello --package-version="${UH_VERSION}" \
	--msgid-bugs-address=https://github.com/ventura8/ubuntu-hello/issues \
	-o "$CORE_PO/ubuntu-hello.pot"

echo "Extracting ubuntu-hello-gtk.pot (version ${UH_VERSION})..."
xgettext --files-from="$GTK_PO/POTFILES" --directory="$ROOT/ubuntu-hello-gtk" \
	--from-code=UTF-8 \
	--keyword=_:1 --keyword=N_ --keyword=ngettext:1,2 --keyword=pgettext:1c,2 \
	--add-comments=TRANSLATORS \
	--package-name=ubuntu-hello-gtk --package-version="${UH_VERSION}" \
	--msgid-bugs-address=https://github.com/ventura8/ubuntu-hello/issues \
	-o "$GTK_PO/ubuntu-hello-gtk.pot"

# Desktop Name/Comment/Keywords for merge_file (append missing msgids only)
python3 - "$GTK_PO/ubuntu-hello-gtk.pot" <<'PY'
import sys
from pathlib import Path
pot = Path(sys.argv[1])
text = pot.read_text(encoding="utf-8")
extras = [
    ("ubuntu-hello-gtk.desktop.in", "Ubuntu Hello"),
    ("ubuntu-hello-gtk.desktop.in", "Windows Hello style facial authentication"),
    ("ubuntu-hello-gtk.desktop.in", "face;recognition;authentication;security;lock;"),
]
add = []
for ref, msgid in extras:
    if f'msgid "{msgid}"' not in text:
        add.append(f'\n#: {ref}\nmsgid "{msgid}"\nmsgstr ""\n')
if add:
    pot.write_text(text + "".join(add), encoding="utf-8")
PY

merge_all() {
	local pot="$1"
	local podir="$2"
	while read -r lang; do
		[[ -z "$lang" ]] && continue
		local po="$podir/${lang}.po"
		if [[ ! -f "$po" ]]; then
			msginit --no-translator --input="$pot" --locale="$lang" --output-file="$po" 2>/dev/null \
				|| {
					cat >"$po" <<EOPO
# Translation for ${lang}.
msgid ""
msgstr ""
"Language: ${lang}\\n"
"Content-Type: text/plain; charset=UTF-8\\n"

EOPO
					msgmerge --quiet --update --backup=none "$po" "$pot"
				}
		else
			msgmerge --quiet --update --backup=none "$po" "$pot"
		fi
	done <"$podir/LINGUAS"
}

echo "Merging ubuntu-hello .po..."
merge_all "$CORE_PO/ubuntu-hello.pot" "$CORE_PO"
echo "Merging ubuntu-hello-gtk .po..."
merge_all "$GTK_PO/ubuntu-hello-gtk.pot" "$GTK_PO"

core_n=$(grep -cve '^$' "$CORE_PO/LINGUAS" || true)
gtk_n=$(grep -cve '^$' "$GTK_PO/LINGUAS" || true)
echo "OK: LINGUAS synced ($core_n / $gtk_n langs), pots refreshed, po merged."
echo "Note: leave msgstr empty unless providing real translations (no fake/MT)."
