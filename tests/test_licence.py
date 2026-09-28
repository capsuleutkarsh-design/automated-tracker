"""The licence credits (UT Community Licence 2.0, section 5) and the startup check that enforces them."""
import os
import shutil

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core import licence

ROOT = licence.program_dir()


def test_the_real_files_pass():
    assert licence.file_problems() == []


def test_line_endings_do_not_matter(tmp_path):
    with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
        text = f.read()
    with open(tmp_path / "LICENSE.md", "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)
    shutil.copy(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), tmp_path)
    assert licence.file_problems(str(tmp_path)) == []


def test_changed_or_missing_files_are_caught(tmp_path):
    assert len(licence.file_problems(str(tmp_path))) == 2
    with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
        text = f.read()
    (tmp_path / "LICENSE.md").write_text(text.replace("No selling", "Selling"), encoding="utf-8")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("# Notices\n", encoding="utf-8")
    assert licence.file_problems(str(tmp_path)) == ["LICENSE.md has been changed",
                                                    "THIRD_PARTY_NOTICES.md has been changed"]


def test_plain_text_licence_for_the_installer():
    text = licence.plain_text(os.path.join(ROOT, "LICENSE.md"))
    assert "<div" not in text and "**" not in text
    assert "No selling" in text and licence.CREDIT_LINE in text


def _window(label):
    w = QWidget()
    QVBoxLayout(w).addWidget(label)
    return w


def test_credit_label_passes_and_hidden_or_changed_fails():
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])  # noqa: F841 - widgets need one
    assert licence.window_problems(_window(licence.credit_label())) == []
    hidden = licence.credit_label()
    w = _window(hidden)
    hidden.hide()
    assert licence.window_problems(w)
    assert licence.window_problems(_window(QLabel(licence.CREDIT_LINE.replace("Utkarsh Tripathi", "X"))))
