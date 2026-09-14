"""GTK 4.10 deprecated widgets must not come back into the GTK app (GTK 4 good practice)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ubuntu-hello-gtk" / "src"
DEPRECATED_PY = re.compile(r"\b[Gg]tk\.(TreeView|ListStore|TreeStore|CellRenderer\w*|TreePath|TreeSelection|ComboBoxText|ComboBox|"
                           r"MessageDialog|Dialog|InfoBar|Assistant|AppChooser\w*|FileChooserDialog|ColorChooser\w*|FontChooser\w*|"
                           r"IconView|Statusbar|LockButton|VolumeButton|EntryCompletion|StyleContext\(\))\b")
DEPRECATED_UI = ("GtkTreeView", "GtkListStore", "GtkComboBoxText", "GtkComboBox", "GtkMessageDialog", "GtkDialog",
                 "GtkInfoBar", "GtkAssistant", "GtkCellRenderer", "GtkIconView", "GtkStatusbar", "GtkLockButton")


def _docstring_lines(source):
	import ast
	lines = set()
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
			first = node.body[0]
			if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and isinstance(first.value.value, str):
				lines.update(range(first.lineno, first.end_lineno + 1))
	return lines


def _code_lines(path):
	source = path.read_text(encoding="utf-8")
	skip = _docstring_lines(source)
	for n, line in enumerate(source.splitlines(), 1):
		stripped = line.split("#", 1)[0]
		if stripped.strip() and n not in skip:
			yield n, stripped


def test_no_deprecated_widgets_in_python():
	hits = []
	for path in sorted(SRC.glob("*.py")):
		for n, line in _code_lines(path):
			match = DEPRECATED_PY.search(line)
			if match and "deprecated" not in line.lower():
				hits.append(f"{path.name}:{n}: {match.group(0)}")
	assert hits == [], "\n".join(hits)


def test_no_deprecated_widgets_in_ui_files():
	hits = []
	for path in sorted(SRC.glob("*.ui")):
		for cls in re.findall(r'class="(Gtk[A-Za-z]+)"', path.read_text(encoding="utf-8")):
			if cls in DEPRECATED_UI:
				hits.append(f"{path.name}: {cls}")
	assert hits == [], "\n".join(hits)
