# -*- coding: utf-8 -*-
"""
Maya 2024 批量重命名工具
======================================
提供批量重命名、前后缀添加、查找替换、正则替换、大小写转换、清理等常用功能。
"""

import re
import maya.cmds as cmds
import maya.OpenMayaUI as omui

from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def get_maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    if ptr is not None:
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    return None


def is_intermediate_shape(node):
    try:
        if cmds.objectType(node, isAType="shape"):
            return bool(cmds.getAttr(node + ".intermediateObject"))
    except Exception:
        pass
    return False


def is_shape(node):
    try:
        return cmds.objectType(node, isAType="shape")
    except Exception:
        return False


def to_uuid(node):
    try:
        uuids = cmds.ls(node, uuid=True) or []
        return uuids[0] if uuids else None
    except Exception:
        return None


def from_uuid(uuid):
    try:
        res = cmds.ls(uuid, long=True) or []
        return res[0] if res else None
    except Exception:
        return None


def get_target_uuids(include_hierarchy=False):
    """
    返回 (uuid_list, top_uuids, warning)。
    - uuid_list 按 Outliner 自然顺序(children 顺序) DFS 排列, 顶层选中节点在前, 子孙紧随其后。
    - top_uuids 为用户显式选中的顶层节点 uuid 集合 (层级模式下需要区分"组"与"组内资产")。
    - 永远排除所有 shape 节点 (含 intermediateObject)。
    """
    sel = cmds.ls(selection=True, long=True) or []
    if not sel:
        return [], set(), ""

    # 保持用户选择顺序, 去重但不排序
    seen = set()
    top_sel = []
    for n in sel:
        if n not in seen and not is_shape(n):
            seen.add(n)
            top_sel.append(n)

    ordered = []
    multi = []

    def walk(node):
        if is_shape(node):
            return
        ordered.append(node)
        if include_hierarchy:
            try:
                if cmds.objectType(node) == "transform":
                    shapes = cmds.listRelatives(node, shapes=True, fullPath=True,
                                                noIntermediate=True) or []
                    if len(shapes) >= 2:
                        multi.append(short_name(node))
            except Exception:
                pass
            children = cmds.listRelatives(node, children=True, fullPath=True,
                                          type="transform") or []
            for c in children:
                walk(c)

    for n in top_sel:
        walk(n)

    # 路径级去重 (保持先出现顺序)
    uniq = []
    seen2 = set()
    for n in ordered:
        if n not in seen2:
            seen2.add(n)
            uniq.append(n)

    uuids = [u for u in (to_uuid(n) for n in uniq) if u]
    top_uuids = set(u for u in (to_uuid(n) for n in top_sel) if u)

    warning = ""
    if multi:
        warning = (u"⚠️ 发现 {0} 个多 Shape Transform ({1}...), "
                   u"其 shape 已自动跳过。".format(len(multi), multi[0]))
    return uuids, top_uuids, warning


def short_name(long_path):
    return long_path.split("|")[-1].split(":")[-1]


def safe_rename(long_path, new_short_name):
    try:
        new_short_name = re.sub(r"[^A-Za-z0-9_]", "_", new_short_name)
        if new_short_name and new_short_name[0].isdigit():
            new_short_name = "_" + new_short_name
        return cmds.rename(long_path, new_short_name)
    except Exception as e:
        cmds.warning(u"[重命名失败] {0} -> {1} : {2}".format(long_path, new_short_name, e))
        return None


TYPE_SUFFIX_MAP = {
    "mesh": "GEO", "transform": "GRP", "joint": "JNT", "locator": "LOC",
    "camera": "CAM", "nurbsCurve": "CRV", "nurbsSurface": "SURF",
    "ikHandle": "IK", "cluster": "CLS", "lattice": "LAT",
    "pointLight": "LGT", "directionalLight": "LGT",
    "spotLight": "LGT", "areaLight": "LGT",
}


def detect_type_suffix(node):
    node_type = cmds.nodeType(node)
    if node_type == "transform":
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True,
                                    noIntermediate=True) or []
        if shapes:
            return TYPE_SUFFIX_MAP.get(cmds.nodeType(shapes[0]), "GRP")
        return "GRP"
    return TYPE_SUFFIX_MAP.get(node_type, node_type.upper())


# ---------------------------------------------------------------------------
# 正则模板
# ---------------------------------------------------------------------------
def _repl_swap_lr(m):
    s = m.group(0)
    return s.replace("L", "\x00").replace("R", "L").replace("\x00", "R") \
            .replace("l", "\x01").replace("r", "l").replace("\x01", "r")


def _repl_upper_suffix(m):
    return "_" + m.group(1).upper()


REGEX_PRESETS = [
    (u"-- 无 --",                                 "",   "",   False),

    (u"── 段落增删 ──",                          None, None, None),
    (u"删除首部段 (a_b_c → b_c)",                r"^[^_]+_",           "",     True),
    (u"删除尾部段 (a_b_c → a_b)",                r"_[^_]+$",           "",     True),
    (u"只保留首部段 (a_b_c → a)",                r"_.*$",              "",     True),
    (u"只保留尾部段 (a_b_c → c)",                r"^.*_",              "",     True),

    (u"── 左右镜像 ──",                          None, None, None),
    (u"L ↔ R 互换 (前后缀通用)",                 r"(?:^|_)[LRlr](?=_|\d|$)", _repl_swap_lr, True),
    (u"Lf ↔ Rt 互换 (尾部)",                     r"_Lf(\d*)$",         r"_Rt\1", True),

    (u"── Maya 残留清理 ──",                     None, None, None),
    (u"去除 Maya 默认前缀 (pCube1→Cube1)",       r"^(p|n|c|polySurface|place2dTexture|group|pSphere|pCube|pCylinder|pPlane|pTorus|pCone|nParticle|nurbs)(?=[A-Z0-9])",
        "", True),
    (u"去除 Shape 后缀 (含 Orig/Deformed)",      r"Shape(Orig|Deformed)?\d*$",  "", True),
    (u"清除粘贴/复制残留 (pasted/copy)",          r"^(pasted__|Copy_of_)|__pasted\d*$|_?[cC]opy\d*$",
        "", True),

    (u"── 规范化 ──",                            None, None, None),
    (u"后缀小写→大写 (_geo→_GEO)",              r"_([a-z]+)$",
        _repl_upper_suffix, True),
    (u"空格 → 下划线",                            r"\s+",               "_",    True),
    (u"多下划线合并 (__ → _)",                   r"_+",                "_",    True),
    (u"去除冒号 (命名空间残留)",                 r":",                 "",     True),
    (u"去除非 ASCII (中文/特殊符号)",            r"[^\x00-\x7F]+",     "",     True),
    (u"合并相邻重复段 (body_body→body)",         r"([A-Za-z0-9]+)_\1(_|$)", r"\1\2", True),
]


# 占位符默认值
DEFAULT_BASE_NAME = "Asset"
DEFAULT_PREFIX = "M01_"
DEFAULT_SUFFIX = "_HD"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class AssetRenamerUI(QtWidgets.QDialog):

    WINDOW_TITLE = u"批量重命名工具"

    def __init__(self, parent=None):
        parent = parent or get_maya_main_window()
        super(AssetRenamerUI, self).__init__(parent)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumWidth(340)
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)

        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(6)

        self.hierarchy_cb = QtWidgets.QCheckBox(u"包含所有子级 (层级模式)")
        main_layout.addWidget(self.hierarchy_cb)

        # ① 批量重命名
        grp_base = QtWidgets.QGroupBox(u"① 批量重命名 (基础名 + 编号)")
        v_base = QtWidgets.QVBoxLayout(grp_base)
        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel(u"基础名:"))
        self.base_name_le = QtWidgets.QLineEdit()
        self.base_name_le.setPlaceholderText(DEFAULT_BASE_NAME)
        row1.addWidget(self.base_name_le)
        v_base.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel(u"起始编号:"))
        self.start_num_sp = QtWidgets.QSpinBox()
        self.start_num_sp.setRange(0, 99999)
        self.start_num_sp.setValue(1)
        self.start_num_sp.setFixedWidth(70)
        row2.addWidget(self.start_num_sp)
        row2.addSpacing(10)
        row2.addWidget(QtWidgets.QLabel(u"编号位数:"))
        self.padding_sp = QtWidgets.QSpinBox()
        self.padding_sp.setRange(1, 6)
        self.padding_sp.setValue(2)
        self.padding_sp.setFixedWidth(50)
        row2.addWidget(self.padding_sp)
        row2.addStretch(1)
        v_base.addLayout(row2)

        self.btn_base_rename = QtWidgets.QPushButton(u"执行批量重命名")
        v_base.addWidget(self.btn_base_rename)
        main_layout.addWidget(grp_base)

        # ② 前后缀
        grp_fix = QtWidgets.QGroupBox(u"② 添加前缀 / 后缀")
        h = QtWidgets.QGridLayout(grp_fix)
        self.prefix_le = QtWidgets.QLineEdit()
        self.prefix_le.setPlaceholderText(DEFAULT_PREFIX)
        self.suffix_le = QtWidgets.QLineEdit()
        self.suffix_le.setPlaceholderText(DEFAULT_SUFFIX)
        self.btn_add_prefix = QtWidgets.QPushButton(u"添加前缀")
        self.btn_add_suffix = QtWidgets.QPushButton(u"添加后缀")
        h.addWidget(QtWidgets.QLabel(u"前缀:"), 0, 0)
        h.addWidget(self.prefix_le, 0, 1)
        h.addWidget(self.btn_add_prefix, 0, 2)
        h.addWidget(QtWidgets.QLabel(u"后缀:"), 1, 0)
        h.addWidget(self.suffix_le, 1, 1)
        h.addWidget(self.btn_add_suffix, 1, 2)
        main_layout.addWidget(grp_fix)

        # ③ 查找替换
        grp_rep = QtWidgets.QGroupBox(u"③ 查找替换")
        g = QtWidgets.QGridLayout(grp_rep)
        g.addWidget(QtWidgets.QLabel(u"常用模板:"), 0, 0)
        self.preset_cb = QtWidgets.QComboBox()
        self._populate_presets()
        g.addWidget(self.preset_cb, 0, 1)

        self.search_le = QtWidgets.QLineEdit()
        self.replace_le = QtWidgets.QLineEdit()
        g.addWidget(QtWidgets.QLabel(u"查找:"), 1, 0)
        g.addWidget(self.search_le, 1, 1)
        g.addWidget(QtWidgets.QLabel(u"替换:"), 2, 0)
        g.addWidget(self.replace_le, 2, 1)
        self.regex_cb = QtWidgets.QCheckBox(u"使用正则表达式")
        g.addWidget(self.regex_cb, 3, 0, 1, 2)
        self.btn_replace = QtWidgets.QPushButton(u"执行替换")
        g.addWidget(self.btn_replace, 4, 0, 1, 2)
        main_layout.addWidget(grp_rep)

        # ④ 大小写
        grp_case = QtWidgets.QGroupBox(u"④ 大小写转换")
        hc = QtWidgets.QHBoxLayout(grp_case)
        self.btn_upper = QtWidgets.QPushButton(u"全部大写")
        self.btn_lower = QtWidgets.QPushButton(u"全部小写")
        self.btn_title = QtWidgets.QPushButton(u"首字母大写")
        for b in (self.btn_upper, self.btn_lower, self.btn_title):
            hc.addWidget(b)
        main_layout.addWidget(grp_case)

        # ⑤ 移除字符
        grp_trim = QtWidgets.QGroupBox(u"⑤ 移除字符")
        gt = QtWidgets.QGridLayout(grp_trim)
        self.trim_head_sp = QtWidgets.QSpinBox()
        self.trim_head_sp.setRange(0, 50)
        self.trim_tail_sp = QtWidgets.QSpinBox()
        self.trim_tail_sp.setRange(0, 50)
        self.btn_trim = QtWidgets.QPushButton(u"移除首/尾 N 个字符")
        gt.addWidget(QtWidgets.QLabel(u"从头移除:"), 0, 0)
        gt.addWidget(self.trim_head_sp, 0, 1)
        gt.addWidget(QtWidgets.QLabel(u"从尾移除:"), 1, 0)
        gt.addWidget(self.trim_tail_sp, 1, 1)
        gt.addWidget(self.btn_trim, 2, 0, 1, 2)
        main_layout.addWidget(grp_trim)

        # ⑥ 其它
        grp_other = QtWidgets.QGroupBox(u"⑥ 其它工具")
        ho = QtWidgets.QHBoxLayout(grp_other)
        self.btn_clean_ns = QtWidgets.QPushButton(u"清理命名空间")
        self.btn_type_suffix = QtWidgets.QPushButton(u"按类型加后缀")
        for btn in (self.btn_clean_ns, self.btn_type_suffix):
            btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                              QtWidgets.QSizePolicy.Fixed)
            btn.setFixedHeight(30)
        ho.addWidget(self.btn_clean_ns)
        ho.addWidget(self.btn_type_suffix)
        main_layout.addWidget(grp_other)

        self.status_lb = QtWidgets.QLabel(u"就绪。请先在场景中选择对象。")
        self.status_lb.setStyleSheet("color: #7fb3ff;")
        self.status_lb.setWordWrap(True)
        main_layout.addWidget(self.status_lb)

    def _populate_presets(self):
        model = self.preset_cb.model()
        for i, preset in enumerate(REGEX_PRESETS):
            name = preset[0]
            self.preset_cb.addItem(name)
            if name.startswith(u"──"):
                item = model.item(i)
                item.setEnabled(False)
                item.setSelectable(False)

    def _connect_signals(self):
        self.btn_base_rename.clicked.connect(self.do_base_rename)
        self.btn_add_prefix.clicked.connect(self.do_add_prefix)
        self.btn_add_suffix.clicked.connect(self.do_add_suffix)
        self.btn_replace.clicked.connect(self.do_replace)
        self.btn_upper.clicked.connect(lambda: self.do_case("upper"))
        self.btn_lower.clicked.connect(lambda: self.do_case("lower"))
        self.btn_title.clicked.connect(lambda: self.do_case("title"))
        self.btn_trim.clicked.connect(self.do_trim)
        self.btn_clean_ns.clicked.connect(self.do_clean_namespace)
        self.btn_type_suffix.clicked.connect(self.do_type_suffix)
        self.preset_cb.currentIndexChanged.connect(self._apply_preset)

    def _apply_preset(self, idx):
        # 切回 "-- 无 --" 时, 清空查找/替换框并取消正则勾选
        if idx == 0:
            self.search_le.clear()
            self.replace_le.clear()
            self.replace_le.setEnabled(True)
            self.regex_cb.setChecked(False)
            self._set_status(u"已重置查找替换。")
            return

        name, src, dst, is_regex = REGEX_PRESETS[idx][:4]
        if src is None:  # 分组标题
            self.preset_cb.setCurrentIndex(0)
            return

        self.search_le.setText(src)
        if callable(dst):
            self.replace_le.setText(u"<自动处理>")
            self.replace_le.setEnabled(False)
        else:
            self.replace_le.setText(dst)
            self.replace_le.setEnabled(True)
        self.regex_cb.setChecked(is_regex)
        self._set_status(u"已套用模板: {0}".format(name))

    def _get_uuids(self):
        uuids, _top, warn = get_target_uuids(self.hierarchy_cb.isChecked())
        if not uuids:
            self._set_status(u"⚠️ 未选中任何对象!", error=True)
            return []
        if warn:
            self._set_status(warn, warning=True)
        return uuids

    def _get_uuids_ex(self):
        """返回 (uuids, top_uuids)。用于需要区分顶层选中与子孙的场景。"""
        uuids, top, warn = get_target_uuids(self.hierarchy_cb.isChecked())
        if not uuids:
            self._set_status(u"⚠️ 未选中任何对象!", error=True)
            return [], set()
        if warn:
            self._set_status(warn, warning=True)
        return uuids, top

    def _set_status(self, msg, error=False, warning=False):
        if error:
            color = "#ff7f7f"
        elif warning:
            color = "#ffd27f"
        else:
            color = "#7fffb3"
        self.status_lb.setStyleSheet("color: {0};".format(color))
        self.status_lb.setText(msg)

    # ---- 功能 ----
    def do_base_rename(self):
        uuids, top_uuids = self._get_uuids_ex()
        if not uuids:
            return
        # 占位符兜底
        base = self.base_name_le.text() or DEFAULT_BASE_NAME
        # 注意: 用户要求下划线并入基础名, 因此直接拼接编号, 不再额外加 _
        # 组名(顶层)显示时去掉末尾下划线, 更自然 (Asset_ -> Asset)
        base_for_group = base.rstrip("_") or base
        start = self.start_num_sp.value()
        padding = self.padding_sp.value()

        # ---- 分配编号 ----
        # 层级模式: 顶层所选 transform 使用 base_for_group (不编号),
        #           其余 (子孙) 按 Outliner 顺序 01, 02, 03... 编号
        # 非层级模式: 所有选中对象按选择顺序编号
        uuid_to_name = {}
        if self.hierarchy_cb.isChecked() and top_uuids:
            idx = 0
            for u in uuids:
                if u in top_uuids:
                    uuid_to_name[u] = base_for_group
                else:
                    uuid_to_name[u] = "{0}{1}".format(
                        base, str(start + idx).zfill(padding))
                    idx += 1
            # 顶层同名冲突时, Maya 会自动加 1/2/3 后缀, 这里不额外处理
        else:
            for i, u in enumerate(uuids):
                uuid_to_name[u] = "{0}{1}".format(
                    base, str(start + i).zfill(padding))

        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            # 执行顺序: 按当前深度倒序, 保证父级改名时子级路径仍然有效
            pending = list(uuids)
            while pending:
                with_path = [(u, from_uuid(u)) for u in pending]
                with_path = [(u, p) for u, p in with_path if p]
                if not with_path:
                    break
                with_path.sort(key=lambda x: x[1].count("|"), reverse=True)
                u, path = with_path[0]
                if safe_rename(path, uuid_to_name[u]):
                    count += 1
                pending.remove(u)
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已重命名 {0} 个对象。".format(count))

    def do_add_prefix(self):
        prefix = self.prefix_le.text().strip() or DEFAULT_PREFIX
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if path and safe_rename(path, prefix + short_name(path)):
                    count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已添加前缀 {0} 个对象。".format(count))

    def do_add_suffix(self):
        suffix = self.suffix_le.text().strip() or DEFAULT_SUFFIX
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if path and safe_rename(path, short_name(path) + suffix):
                    count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已添加后缀 {0} 个对象。".format(count))

    def do_replace(self):
        idx = self.preset_cb.currentIndex()
        preset_repl = None
        if idx > 0 and REGEX_PRESETS[idx][2] is not None and callable(REGEX_PRESETS[idx][2]):
            preset_repl = REGEX_PRESETS[idx][2]

        src = self.search_le.text()
        dst = self.replace_le.text()
        if not src:
            self._set_status(u"⚠️ 查找内容为空!", error=True)
            return
        use_regex = self.regex_cb.isChecked()
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if not path:
                    continue
                old = short_name(path)
                try:
                    if preset_repl is not None:
                        new = re.sub(src, preset_repl, old)
                    elif use_regex:
                        new = re.sub(src, dst, old)
                    else:
                        new = old.replace(src, dst)
                except re.error as e:
                    self._set_status(u"⚠️ 正则错误: {0}".format(e), error=True)
                    return
                if new != old and safe_rename(path, new):
                    count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已替换 {0} 个对象。".format(count))

    def do_case(self, mode):
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if not path:
                    continue
                old = short_name(path)
                if mode == "upper":
                    new = old.upper()
                elif mode == "lower":
                    new = old.lower()
                else:
                    new = "_".join(p[:1].upper() + p[1:] for p in old.split("_") if p)
                if new != old and safe_rename(path, new):
                    count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 大小写转换完成, 处理 {0} 个对象。".format(count))

    def do_trim(self):
        head = self.trim_head_sp.value()
        tail = self.trim_tail_sp.value()
        if head == 0 and tail == 0:
            self._set_status(u"⚠️ 首尾移除数均为 0!", error=True)
            return
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if not path:
                    continue
                old = short_name(path)
                new = old[head: len(old) - tail] if tail else old[head:]
                if not new:
                    cmds.warning(u"[跳过] 裁剪后名字为空: {0}".format(old))
                    continue
                if new != old and safe_rename(path, new):
                    count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已裁剪 {0} 个对象。".format(count))

    def do_clean_namespace(self):
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if not path:
                    continue
                shortn = path.split("|")[-1]
                if ":" in shortn:
                    if safe_rename(path, shortn.split(":")[-1]):
                        count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已清理 {0} 个对象的命名空间。".format(count))

    def do_type_suffix(self):
        uuids = self._get_uuids()
        if not uuids:
            return
        count = 0
        cmds.undoInfo(openChunk=True)
        try:
            for u in uuids:
                path = from_uuid(u)
                if not path:
                    continue
                old = short_name(path)
                suffix = detect_type_suffix(path)
                if old.upper().endswith("_" + suffix):
                    continue
                if safe_rename(path, "{0}_{1}".format(old, suffix)):
                    count += 1
        finally:
            cmds.undoInfo(closeChunk=True)
        self._set_status(u"✅ 已为 {0} 个对象追加类型后缀。".format(count))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
_WIN_INSTANCE = None


def show_window():
    global _WIN_INSTANCE
    try:
        if _WIN_INSTANCE is not None:
            _WIN_INSTANCE.close()
            _WIN_INSTANCE.deleteLater()
    except Exception:
        pass
    _WIN_INSTANCE = AssetRenamerUI()
    _WIN_INSTANCE.show()
    return _WIN_INSTANCE


if __name__ == "__main__":
    show_window()


def UI():
    """小它工具箱主入口兼容:EzRename.UI()"""
    return show_window()