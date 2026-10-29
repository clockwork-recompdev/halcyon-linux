"""Audio CD dialogs: play the disc, or rip it with editable track names."""
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QProgressDialog, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import cd


def read_disc(parent):
    dev = cd.find_drive()
    if not dev:
        QMessageBox.information(parent, "Audio CD", "No CD drive found.")
        return None
    try:
        tracks, leadout = cd.read_toc(dev)
    except OSError:
        QMessageBox.information(parent, "Audio CD", "Insert an audio CD first.")
        return None
    if not cd.audio_tracks(tracks):
        QMessageBox.information(parent, "Audio CD", "This disc has no audio tracks.")
        return None
    return dev, tracks, leadout


def play_cd(parent, player, info=None):
    disc = read_disc(parent)
    if disc:
        dev, tracks, leadout = disc
        if info is None:
            info = cd.lookup(tracks, leadout)
        player.open_titled(cd.play_entries(dev, tracks, info))


class _Worker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(list, str)


class RipDialog(QDialog):
    def __init__(self, parent, dev, tracks, leadout):
        super().__init__(parent)
        self.setWindowTitle("Rip Audio CD")
        self.resize(560, 560)
        self.dev, self.tracks = dev, cd.audio_tracks(tracks)
        info = cd.lookup(tracks, leadout) or {}
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.artist = QLineEdit(info.get("artist", ""))
        self.album = QLineEdit(info.get("album", ""))
        self.year = QLineEdit(info.get("year", ""))
        for w, ph in ((self.artist, "Artist"), (self.album, "Album"), (self.year, "Year")):
            w.setPlaceholderText(ph)
        form.addRow("Artist", self.artist)
        form.addRow("Album", self.album)
        form.addRow("Year", self.year)
        lay.addLayout(form)

        self.table = QTableWidget(len(self.tracks), 2)
        self.table.setHorizontalHeaderLabels(["#", "Title"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for row, (no, _, _) in enumerate(self.tracks):
            num = QTableWidgetItem(f"{no:02d}")
            num.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            num.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, num)
            self.table.setItem(row, 1, QTableWidgetItem(info.get("titles", {}).get(no) or f"Track {no:02d}"))
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.fmt = QComboBox()
        self.fmt.addItems(list(cd.FORMATS))
        row.addWidget(self.fmt)
        self.dest = QLineEdit()
        row.addWidget(self.dest, 1)
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)
        for w in (self.artist, self.album):
            w.textChanged.connect(self._default_dest)
        self._default_dest()

        buttons = QDialogButtonBox()
        buttons.addButton("Rip", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.start)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        if not info:
            self.setWindowTitle("Rip Audio CD — not found on MusicBrainz, type the names in")

    def _default_dest(self):
        if getattr(self, "_custom_dest", False):
            return
        self.dest.setText(os.path.join(os.path.expanduser("~/Music"),
                                       cd.safe_name(self.artist.text() or "Unknown Artist"),
                                       cd.safe_name(self.album.text() or "Unknown Album")))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Rip to", self.dest.text())
        if d:
            self._custom_dest = True
            self.dest.setText(d)

    def start(self):
        jobs = []
        for row, (no, _, _) in enumerate(self.tracks):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                jobs.append((no, self.table.item(row, 1).text().strip() or f"Track {no:02d}"))
        if not jobs:
            return
        meta = {"artist": self.artist.text().strip(), "album": self.album.text().strip(),
                "year": self.year.text().strip()}
        prog = QProgressDialog("Starting…", "Cancel", 0, len(jobs), self)
        prog.setWindowTitle("Ripping")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        worker = _Worker()
        cancel = threading.Event()
        prog.canceled.connect(cancel.set)
        worker.progress.connect(lambda d, n, msg: (prog.setValue(d), prog.setLabelText(msg)))

        def done(files, error):
            prog.close()
            if error:
                QMessageBox.warning(self, "Ripping", error)
            else:
                QMessageBox.information(self, "Ripping", f"Ripped {len(files)} track(s) to\n{self.dest.text()}")
                self.accept()
        worker.finished.connect(done)

        fmt, dest = self.fmt.currentText(), self.dest.text()

        def run():
            try:
                files = cd.rip(self.dev, jobs, fmt, dest, meta,
                               progress=worker.progress.emit, cancelled=cancel.is_set)
                worker.finished.emit(files, "")
            except Exception as e:      # reported in the dialog
                worker.finished.emit([], str(e))
        self._worker = worker
        threading.Thread(target=run, daemon=True).start()


def rip_cd(parent):
    disc = read_disc(parent)
    if disc:
        RipDialog(parent, *disc).exec()
