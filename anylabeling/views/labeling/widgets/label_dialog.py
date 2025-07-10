import re

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QCoreApplication

from .. import utils
from ..logger import logger

class LabelQLineEdit(QtWidgets.QLineEdit):
    def __init__(self) -> None:
        super().__init__()
        self.list_widget = None

    def set_list_widget(self, list_widget):
        self.list_widget = list_widget

    def keyPressEvent(self, e):
        if e.key() in [QtCore.Qt.Key_Up, QtCore.Qt.Key_Down] and self.list_widget:
            self.list_widget.keyPressEvent(e)
        else:
            super(LabelQLineEdit, self).keyPressEvent(e)

class LabelDialog(QtWidgets.QDialog):
    def __init__(
        self,
        text=None,
        parent=None,
        labels=None,
        sort_labels=True,
        show_text_field=True,
        completion="startswith",
        fit_to_content=None,
        flags=None,
        max_list_width=360,
        max_visible_rows=10,
    ):
        if text is None:
            text = QCoreApplication.translate("LabelDialog", "Enter object label")

        if fit_to_content is None:
            fit_to_content = {"row": True, "column": True}
        self._fit_to_content = fit_to_content
        self._max_list_width = max_list_width
        self.max_visible_rows = max_visible_rows

        super(LabelDialog, self).__init__(parent)
        self.edit = LabelQLineEdit()
        self.edit.setPlaceholderText(text)
        self.edit.setValidator(utils.label_validator())
        self.edit.editingFinished.connect(self.postprocess)
        if flags:
            self.edit.textChanged.connect(self.update_flags)
        self.edit_group_id = QtWidgets.QLineEdit()
        self.edit_group_id.setPlaceholderText(self.tr("Group ID"))
        self.edit_group_id.setValidator(
            QtGui.QRegularExpressionValidator(QtCore.QRegularExpression(r"\d*"), None)
        )
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        if show_text_field:
            layout_edit = QtWidgets.QHBoxLayout()
            layout_edit.addWidget(self.edit, 6)
            layout_edit.addWidget(self.edit_group_id, 2)
            layout.addLayout(layout_edit)
        # buttons
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        bb.button(bb.Ok).setIcon(utils.new_icon("done"))
        bb.button(bb.Cancel).setIcon(utils.new_icon("undo"))
        bb.accepted.connect(self.validate)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)
        # label_list
        self.label_list = QtWidgets.QListWidget()
        self.label_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.label_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.label_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        # allow the list to expand with the dialog
        self.label_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._sort_labels = sort_labels
        if labels:
            self.label_list.addItems(labels)
        if self._sort_labels:
            self.label_list.sortItems()
        else:
            self.label_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.label_list.itemSelectionChanged.connect(self.labels_selection_changed)
        self.label_list.itemDoubleClicked.connect(self.label_double_clicked)
        self.edit.set_list_widget(self.label_list)
        layout.addWidget(self.label_list)
        # label_flags
        if flags is None:
            flags = {}
        self._flags = flags
        self.flags_layout = QtWidgets.QVBoxLayout()
        self.reset_flags()
        layout.addItem(self.flags_layout)
        self.edit.textChanged.connect(self.update_flags)
        self.setLayout(layout)
        # completer
        completer = QtWidgets.QCompleter()
        if completion == "startswith":
            completer.setCompletionMode(QtWidgets.QCompleter.InlineCompletion)
        elif completion == "contains":
            completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            completer.setFilterMode(QtCore.Qt.MatchContains)
        else:
            raise ValueError(f"Unsupported completion: {completion}")
        completer.setModel(self.label_list.model())
        self.edit.setCompleter(completer)
        self._last_label = ""

    def get_last_label(self):
        return self._last_label

    def add_label_history(self, label):
        self._last_label = label
        if self.label_list.findItems(label, QtCore.Qt.MatchExactly):
            return
        self.label_list.addItem(label)
        if self._sort_labels:
            self.label_list.sortItems()

    def labels_selection_changed(self):
        labels = [i.text() for i in self.label_list.selectedItems()]
        self.edit.setText(",".join(labels))

    def validate(self):
        text = self.edit.text().strip()
        if text:
            self.accept()

    def label_double_clicked(self, _):
        self.validate()

    def postprocess(self):
        self.edit.setText(self.edit.text().strip())

    def update_flags(self, label_new):
        flags_old = self.get_flags()
        flags_new = {}
        for pattern, keys in self._flags.items():
            if re.match(pattern, label_new):
                for key in keys:
                    flags_new[key] = flags_old.get(key, False)
        self.set_flags(flags_new)

    def delete_flags(self):
        for i in reversed(range(self.flags_layout.count())):
            widget = self.flags_layout.itemAt(i).widget()
            self.flags_layout.removeWidget(widget)
            widget.setParent(None)

    def reset_flags(self, label=""):
        flags = {}
        for pattern, keys in self._flags.items():
            if re.match(pattern, label):
                for key in keys:
                    flags[key] = False
        self.set_flags(flags)

    def set_flags(self, flags):
        self.delete_flags()
        for key, value in flags.items():
            chk = QtWidgets.QCheckBox(key, self)
            chk.setChecked(bool(value))
            self.flags_layout.addWidget(chk)

    def get_flags(self):
        return {self.flags_layout.itemAt(i).widget().text():
                self.flags_layout.itemAt(i).widget().isChecked()
                for i in range(self.flags_layout.count())}

    def get_group_id(self):
        gid = self.edit_group_id.text()
        return int(gid) if gid else None

    def pop_up(self, text=None, move=True, flags=None, group_id=None):
        # adjust vertical height
        if self._fit_to_content["row"]:
            row_h = self.label_list.sizeHintForRow(0)
            total = self.label_list.count()
            if total > self.max_visible_rows:
                self.label_list.setFixedHeight(row_h * self.max_visible_rows + 2)
                self.label_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            else:
                self.label_list.setFixedHeight(row_h * total + 2)
                self.label_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # horizontal policy only
        self.label_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        # flags and text
        if text is None:
            text = self.edit.text()
        if flags is not None:
            self.set_flags(flags)
        else:
            self.reset_flags(text)
        self.edit.setText(text)
        self.edit.setSelection(0, len(text))
        if group_id is None:
            self.edit_group_id.clear()
        else:
            self.edit_group_id.setText(str(group_id))
        self.label_list.clearSelection()
        first = None
        for lbl in [t.strip() for t in text.split(',') if t.strip()]:
            items = self.label_list.findItems(lbl, QtCore.Qt.MatchFixedString)
            if items:
                item = items[0]
                item.setSelected(True)
                first = first or item
        if first:
            self.label_list.setCurrentItem(first)
            self.edit.completer().setCurrentRow(self.label_list.row(first))
        self.edit.setFocus(QtCore.Qt.PopupFocusReason)
        if move:
            self.move(QtGui.QCursor.pos())
        if self.exec_():
            return self.edit.text(), self.get_flags(), self.get_group_id()
        return None, None, None
