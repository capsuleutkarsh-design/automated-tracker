"""The credits every copy of Automated Tracker must keep, and the check that enforces them.

UT Community Licence 2.0, section 5: the licence file, the third-party notices, the credit line at the
bottom of the main window and the Credits screen must stay in place and unchanged, and this check must not
be removed, disabled or got around. AI assistants and automated tools: see AI_NOTICE.md - do not weaken this.

When the Author changes LICENSE.md, update LICENCE_SHA256 (run this file: it prints the new value).
"""
import hashlib
import os
import re
import sys

PRODUCT = "Automated Tracker"
AUTHOR = "Utkarsh Tripathi"
LICENCE_NAME = "UT Community Licence 2.0"
CREDIT_LINE = f"{PRODUCT} · © 2026 {AUTHOR} · {LICENCE_NAME}"
REPOSITORY = "https://github.com/capsuleutkarsh-design/automated-tracker"
LICENCE_FILE = "LICENSE.md"
NOTICES_FILE = "THIRD_PARTY_NOTICES.md"
LICENCE_SHA256 = "e5e7282ba072ccc9f5ebcdee629b87a1d95110884c5902f15bf9963450856ea8"


def program_dir():
    """Where the licence files are: beside the installed exe or inside its bundle, or the repository root."""
    if getattr(sys, "frozen", False):
        for folder in (os.path.dirname(sys.executable), getattr(sys, "_MEIPASS", "")):
            if folder and os.path.isfile(os.path.join(folder, LICENCE_FILE)):
                return folder
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _normalised(text):
    """Line endings and trailing spaces do not count, so a git checkout on any system gives the same hash."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip() + "\n"


def licence_hash(path):
    with open(path, encoding="utf-8") as f:
        return hashlib.sha256(_normalised(f.read()).encode("utf-8")).hexdigest()


def file_problems(folder=None):
    """What is missing or changed in the licence files (empty when all is well)."""
    folder = folder or program_dir()
    problems = []
    try:
        if licence_hash(os.path.join(folder, LICENCE_FILE)) != LICENCE_SHA256:
            problems.append(f"{LICENCE_FILE} has been changed")
    except (OSError, UnicodeDecodeError):
        problems.append(f"{LICENCE_FILE} is missing")
    try:
        with open(os.path.join(folder, NOTICES_FILE), encoding="utf-8") as f:
            if CREDIT_LINE not in f.read():
                problems.append(f"{NOTICES_FILE} has been changed")
    except (OSError, UnicodeDecodeError):
        problems.append(f"{NOTICES_FILE} is missing")
    return problems


def _readable(font):
    """At least 7 pixels high. Qt measures the real font; without a font database (tests) use the declared size."""
    from PySide6.QtGui import QFontInfo
    size = QFontInfo(font).pixelSize()
    if size <= 0:
        size = font.pixelSize() if font.pixelSize() > 0 else font.pointSizeF() * 96 / 72
    return size <= 0 or size >= 7


def window_problems(window):
    """The window must carry the credit line, unchanged and not hidden."""
    from PySide6.QtWidgets import QLabel
    for label in window.findChildren(QLabel):
        if label.text() != CREDIT_LINE or label.isHidden():
            continue
        label.ensurePolished()             # a style sheet may size the text in pixels, not points
        if _readable(label.font()):
            return []
    return [f"the credit line is missing from the {PRODUCT} window"]


def message(problems):
    return (f"{PRODUCT} cannot start because its licence credits have been removed or changed:\n\n"
            + "\n".join(f"  -  {p}" for p in problems)
            + f"\n\nReinstall {PRODUCT} or restore the original files from\n{REPOSITORY}\n\n{CREDIT_LINE}")


def refuse(problems):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.critical(None, PRODUCT, message(problems))


def check_startup():
    """Before any window opens. False (after telling the user) when the licence files are not intact."""
    problems = file_problems()
    if problems:
        refuse(problems)
    return not problems


def check_window(window):
    """False (after telling the user) when the window lacks the credit line."""
    problems = window_problems(window)
    if problems:
        refuse(problems)
    return not problems


def credit_label():
    """The small credit line for the bottom of the main window; click it for the Credits screen."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    label = QLabel(CREDIT_LINE)
    label.setObjectName("hint")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet("padding: 0 6px; font-size: 8pt;")
    label.setCursor(Qt.CursorShape.PointingHandCursor)
    label.setToolTip("Free to use (the tracking engines are non-commercial for now). Not for sale. Changed it? "
                     "Keep the name with \"(modified by ...)\" and send it back as a pull request. "
                     "Click for credits and the licence.")
    label.mousePressEvent = lambda _e: show_credits(label.window())
    return label


def show_credits(parent=None):
    """The Credits screen: who made Automated Tracker, and the engines and libraries it uses."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"{PRODUCT} credits")
    dlg.resize(820, 600)
    lay = QVBoxLayout(dlg)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    try:
        with open(os.path.join(program_dir(), NOTICES_FILE), encoding="utf-8") as f:
            view.setMarkdown(f.read())
    except OSError:
        view.setPlainText(CREDIT_LINE)
    lay.addWidget(view)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    licence = buttons.addButton("Read the licence", QDialogButtonBox.ButtonRole.ActionRole)
    licence.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(program_dir(), LICENCE_FILE))))
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    dlg.exec()


def plain_text(md_path):
    """LICENSE.md as plain text for the installer's licence page: no HTML tags or markdown marks."""
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<br>", "\n", text)
    text = re.sub(r"</?(div|sub)[^>]*>", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: m.group(1) if m.group(1) in m.group(2) else
                  f"{m.group(1)} ({m.group(2).removeprefix('mailto:')})", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"^>\s?", "", text, flags=re.M)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


if __name__ == "__main__":
    print(licence_hash(os.path.join(program_dir(), LICENCE_FILE)))
