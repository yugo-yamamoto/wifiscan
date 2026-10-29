"""Wi-Fi スキャナ (Windows / tkinter GUI)

wlanapi.dll を ctypes で直接叩いて、周囲の AP を一覧表示する。
netsh と違いスキャンを実際に実行する (WlanScan) ので、キャッシュではなく現在の値が出る。

起動: pythonw.exe wifiscan.pyw
"""

import ctypes as C
import math
import os
import re
import struct
import threading
import queue
import time
import datetime
import winreg
import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox

wlan = C.windll.wlanapi

# ---------------------------------------------------------------- structures

class GUID(C.Structure):
    _fields_ = [("D1", C.c_ulong), ("D2", C.c_ushort),
                ("D3", C.c_ushort), ("D4", C.c_ubyte * 8)]

class DOT11_SSID(C.Structure):
    _fields_ = [("len", C.c_ulong), ("ssid", C.c_ubyte * 32)]

class RATESET(C.Structure):
    _fields_ = [("len", C.c_ulong), ("rates", C.c_ushort * 126)]

class BSS(C.Structure):
    _fields_ = [("ssid", DOT11_SSID), ("phyid", C.c_ulong), ("bssid", C.c_ubyte * 6),
                ("bsstype", C.c_int), ("phytype", C.c_int), ("rssi", C.c_long),
                ("quality", C.c_ulong), ("inreg", C.c_ubyte), ("beacon", C.c_ushort),
                ("ts", C.c_ulonglong), ("hts", C.c_ulonglong), ("cap", C.c_ushort),
                ("freq", C.c_ulong), ("rateset", RATESET),
                ("ieoff", C.c_ulong), ("iesize", C.c_ulong)]

class BSSLIST(C.Structure):
    _fields_ = [("total", C.c_ulong), ("count", C.c_ulong), ("entries", BSS * 1)]

class IFINFO(C.Structure):
    _fields_ = [("guid", GUID), ("desc", C.c_wchar * 256), ("state", C.c_int)]

class IFLIST(C.Structure):
    _fields_ = [("count", C.c_ulong), ("index", C.c_ulong), ("ifs", IFINFO * 1)]

class ASSOC_ATTR(C.Structure):
    _fields_ = [("ssid", DOT11_SSID), ("bsstype", C.c_int), ("bssid", C.c_ubyte * 6),
                ("phytype", C.c_int), ("phyindex", C.c_ulong), ("quality", C.c_ulong),
                ("rxrate", C.c_ulong), ("txrate", C.c_ulong)]

class SEC_ATTR(C.Structure):
    _fields_ = [("secenabled", C.c_int), ("onexenabled", C.c_int),
                ("authalgo", C.c_int), ("cipheralgo", C.c_int)]

class CONN_ATTR(C.Structure):
    _fields_ = [("state", C.c_int), ("mode", C.c_int), ("profile", C.c_wchar * 256),
                ("assoc", ASSOC_ATTR), ("sec", SEC_ATTR)]

PHY = {1: "FHSS", 2: "DSSS", 3: "IR", 4: "11a", 5: "11b", 6: "11g",
       7: "11n", 8: "11ac", 9: "DMG", 10: "11ax", 11: "11be"}


# ---------------------------------------------------------------- app icon

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_BG = (0x0A, 0x84, 0xFF)        # 背景の青
ICON_FG = (0xFF, 0xFF, 0xFF)        # 電波マークの白


def _dib(size, rgba):
    """RGBA を ICO 用の 32bit DIB にする。

    PNG 圧縮エントリは読み手によって対応が割れる (GDI+ が読めない) ので、
    互換性の高い無圧縮 DIB を使う。
    """
    # BITMAPINFOHEADER。高さは XOR + AND マスク分で 2 倍にする決まり。
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)

    rows = []                                   # XOR: 下から上、BGRA 順
    for y in range(size - 1, -1, -1):
        row = bytearray()
        base = y * size * 4
        for x in range(size):
            o = base + x * 4
            row += bytes((rgba[o + 2], rgba[o + 1], rgba[o], rgba[o + 3]))
        rows.append(bytes(row))

    # AND マスクは 32bit アルファを使うので全 0 (不透明扱い) でよい。
    stride = ((size + 31) // 32) * 4
    return hdr + b"".join(rows) + b"\x00" * (stride * size)


def _ico(images):
    """(size, DIB バイト列) のリストから ICO を組み立てる。"""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        b = 0 if size >= 256 else size          # 256 は 0 で表す
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return head + entries + blobs


def _render_wifi(size):
    """角丸四角の上に Wi-Fi の電波マークを描いた RGBA を返す。"""
    cx, cy = size / 2.0, size * 0.78
    radii = (size * 0.22, size * 0.40, size * 0.58)
    half = size * 0.045                          # 円弧の太さの半分
    dot = size * 0.075
    corner = size * 0.22
    x1 = y1 = size - 0.5
    n = 3 if size <= 64 else 2                   # スーパーサンプリング数

    def in_bg(px, py):
        dx = max(0.5 + corner - px, 0.0, px - (x1 - corner))
        dy = max(0.5 + corner - py, 0.0, py - (y1 - corner))
        return dx * dx + dy * dy <= corner * corner

    def in_fg(px, py):
        dx, dy = px - cx, py - cy
        d = math.hypot(dx, dy)
        if d <= dot:                                        # 中心のドット
            return True
        if dy > 0:                                          # 下半分は描かない
            return False
        if abs(math.degrees(math.atan2(dx, -dy))) > 46.0:   # 扇形の範囲
            return False
        return any(abs(d - r) <= half for r in radii)       # 3 本の円弧

    def cover(fn, x, y):
        hit = 0
        for i in range(n):
            for j in range(n):
                if fn(x + (i + 0.5) / n, y + (j + 0.5) / n):
                    hit += 1
        return hit / (n * n)

    buf = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            a = cover(in_bg, x, y)
            if a <= 0.0:
                continue
            m = min(cover(in_fg, x, y) / a, 1.0) if a > 0 else 0.0
            o = (y * size + x) * 4
            for k in range(3):
                buf[o + k] = int(ICON_BG[k] * (1 - m) + ICON_FG[k] * m)
            buf[o + 3] = int(a * 255)
    return bytes(buf)


def ensure_icon():
    """アイコンを生成してパスを返す。生成は初回のみで、以降はキャッシュを使う。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "wifiscan", "wifiscan.ico")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    imgs = [(s, _dib(s, _render_wifi(s))) for s in ICON_SIZES]
    with open(path, "wb") as fp:
        fp.write(_ico(imgs))
    return path


def enable_dpi_awareness():
    """Tk() を作る前に呼ぶこと。呼ばないと Windows が画面を引き伸ばして文字がぼやける。"""
    try:                                    # Windows 10 1703+ : PerMonitorV2
        if C.windll.user32.SetProcessDpiAwarenessContext(C.c_void_p(-4)):
            return "PerMonitorV2"
    except Exception:                       # noqa: BLE001
        pass
    try:                                    # Windows 8.1+ : PerMonitor
        C.windll.shcore.SetProcessDpiAwareness(2)
        return "PerMonitor"
    except Exception:                       # noqa: BLE001
        pass
    try:                                    # Vista+ : System DPI
        C.windll.user32.SetProcessDPIAware()
        return "System"
    except Exception:                       # noqa: BLE001
        return "none"

# ---------------------------------------------------------------- wlan access

def chband(khz):
    m = khz // 1000
    if m < 2500:
        return "2.4GHz", 14 if m == 2484 else (m - 2407) // 5
    if m >= 5925:
        return "6GHz", (m - 5950) // 5
    return "5GHz", (m - 5000) // 5


def bssload(ie):
    """BSS Load 要素 (Element ID 11) から (接続端末数, チャネル使用率 0-255) を取る。"""
    i = 0
    while i + 2 <= len(ie):
        eid, ln = ie[i], ie[i + 1]
        if eid == 11 and ln >= 5:
            b = ie[i + 2:i + 7]
            return int.from_bytes(b[0:2], "little"), b[2]
        i += 2 + ln
    return None, None


def open_handle():
    h, ver = C.c_void_p(), C.c_ulong()
    if wlan.WlanOpenHandle(2, None, C.byref(ver), C.byref(h)) != 0:
        raise OSError("WlanOpenHandle failed")
    return h


IFACE_STATE = {0: "未準備", 1: "接続中", 2: "アドホック", 3: "切断処理中",
               4: "未接続", 5: "関連付け中", 6: "探索中", 7: "認証中"}

# Wi-Fi Direct の仮想アダプタを除外するためのパターン
VIRTUAL_PAT = re.compile(r"virtual|direct", re.I)


def list_interfaces(h):
    """全 WLAN インターフェースを (GUID, 説明, 状態) のリストで返す。

    WlanFreeMemory の前に値をコピーしておくこと。構造体をそのまま持ち回すと
    解放済みメモリを指すビューになり、以降の API 呼び出しが不定に失敗する。
    """
    pil = C.POINTER(IFLIST)()
    rc = wlan.WlanEnumInterfaces(h, None, C.byref(pil))
    if rc != 0:
        raise OSError(f"WlanEnumInterfaces failed (rc={rc})")
    try:
        n = pil.contents.count
        arr = (IFINFO * n).from_address(C.addressof(pil.contents.ifs))
        out = []
        for i in range(n):
            e = arr[i]
            guid = GUID(e.guid.D1, e.guid.D2, e.guid.D3,
                        (C.c_ubyte * 8)(*e.guid.D4))     # 値をコピーする
            out.append((guid, e.desc, e.state))
        return out
    finally:
        wlan.WlanFreeMemory(pil)


def pick_interface(items):
    """接続中のものを最優先し、次に仮想アダプタでないものを選ぶ。"""
    if not items:
        raise OSError("無線インターフェースが見つかりません")
    for it in items:
        if it[2] == 1:                                   # wlan_interface_state_connected
            return it
    for it in items:
        if not VIRTUAL_PAT.search(it[1]):
            return it
    return items[0]


def current_connection(h, guid):
    size, data, vt = C.c_ulong(), C.c_void_p(), C.c_int()
    rc = wlan.WlanQueryInterface(h, C.byref(guid), 7, None,
                                 C.byref(size), C.byref(data), C.byref(vt))
    if rc != 0 or not data:
        return None
    ca = C.cast(data, C.POINTER(CONN_ATTR)).contents
    a = ca.assoc
    out = dict(
        state=ca.state,
        ssid=bytes(a.ssid.ssid[:a.ssid.len]).decode("utf-8", "replace"),
        bssid=":".join(f"{b:02x}" for b in a.bssid),
        rx=a.rxrate // 1000, tx=a.txrate // 1000,
        quality=a.quality, phy=PHY.get(a.phytype, str(a.phytype)),
        profile=ca.profile)
    wlan.WlanFreeMemory(data)
    return out


# ------------------------------------------------- WirelessMode (2.4GHz 可否)

# ネットワークアダプタのドライバ設定が入るクラス GUID
NIC_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"

# WirelessMode のレジストリ値 → 表示名
WMODE = {0:  "2.4GHz のみ (802.11b)",
         16: "2.4GHz のみ (802.11g)",
         32: "2.4GHz のみ (802.11b/g)",
         17: "5GHz のみ (802.11a)",
         18: "デュアルバンド (802.11a/g)",
         34: "デュアルバンド (802.11a/b/g)"}

MODE_DUAL = 34      # 2.4GHz を使う
MODE_5GHZ = 17      # 2.4GHz を使わない


def guid_to_str(g):
    d4 = bytes(g.D4)
    return "{%08X-%04X-%04X-%02X%02X-%s}" % (
        g.D1, g.D2, g.D3, d4[0], d4[1], d4[2:].hex().upper())


def find_adapter_regkey(guid_text):
    """インターフェース GUID に対応するドライバ設定のサブキー名 ('0012' など) を返す。"""
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NIC_CLASS)
    except OSError:
        return None
    with root:
        for i in range(2000):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                return None
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    nid, _ = winreg.QueryValueEx(k, "NetCfgInstanceId")
                if nid.lower() == guid_text.lower():
                    return sub
            except OSError:
                continue
    return None


def read_wireless_mode(sub):
    """現在の WirelessMode を int で返す。未設定なら None。管理者権限は不要。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NIC_CLASS + "\\" + sub) as k:
            v, _ = winreg.QueryValueEx(k, "WirelessMode")
        return int(v)
    except (OSError, ValueError, TypeError):
        return None


def set_wireless_mode_elevated(desc, value):
    """UAC を出して WirelessMode を変更する。True = 昇格プロンプトの起動に成功。"""
    cmd = (f"Set-NetAdapterAdvancedProperty -InterfaceDescription '{desc}' "
           f"-RegistryKeyword 'WirelessMode' -RegistryValue {value}")
    params = f'-NoProfile -ExecutionPolicy Bypass -Command "{cmd}"'
    rc = C.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, 0)
    return rc > 32


def interface_info():
    """スキャンせずにインターフェースの GUID 文字列と説明だけ取る。"""
    h = open_handle()
    try:
        guid, desc, _ = pick_interface(list_interfaces(h))
        return guid_to_str(guid), desc
    finally:
        wlan.WlanCloseHandle(h, None)


def scan(wait=5.0):
    """スキャンを実行して (一覧, 接続情報, インターフェース名, 警告) を返す。"""
    h = open_handle()
    try:
        guid, desc, state = pick_interface(list_interfaces(h))
        warn = ""

        # スキャン中など一時的な理由でも失敗するので、ここでは中断せずキャッシュを読む。
        rc = wlan.WlanScan(h, C.byref(guid), None, None, None)
        if rc != 0:
            warn = f"WlanScan が rc={rc} を返しました（キャッシュを表示します）"
        time.sleep(wait)

        pbl = C.POINTER(BSSLIST)()
        rc = wlan.WlanGetNetworkBssList(h, C.byref(guid), None, 3, 0,
                                        None, C.byref(pbl))
        if rc != 0:
            raise OSError(f"WlanGetNetworkBssList failed (rc={rc}) / "
                          f"{desc} / 状態={IFACE_STATE.get(state, state)}")

        base = C.addressof(pbl.contents.entries)
        rows = {}
        for i in range(pbl.contents.count):
            off = base + i * C.sizeof(BSS)
            e = BSS.from_address(off)
            ie = C.string_at(off + e.ieoff, e.iesize)
            band, ch = chband(e.freq)
            sta, util = bssload(ie)
            mac = ":".join(f"{b:02x}" for b in e.bssid)
            rows[mac] = dict(
                ssid=bytes(e.ssid.ssid[:e.ssid.len]).decode("utf-8", "replace") or "(非公開)",
                bssid=mac, band=band, ch=ch, phy=PHY.get(e.phytype, str(e.phytype)),
                sig=e.quality, rssi=e.rssi,
                sta=-1 if sta is None else sta,
                util=-1 if util is None else util * 100 // 255)
        wlan.WlanFreeMemory(pbl)
        return list(rows.values()), current_connection(h, guid), desc, warn
    finally:
        wlan.WlanCloseHandle(h, None)

# ---------------------------------------------------------------- gui

COLS = [("ssid", "SSID", 190, "w"), ("bssid", "BSSID", 145, "w"),
        ("band", "BAND", 62, "center"), ("ch", "CH", 50, "center"),
        ("phy", "PHY", 55, "center"), ("sig", "SIGNAL", 65, "e"),
        ("rssi", "RSSI", 65, "e"), ("sta", "STA", 48, "e"),
        ("util", "UTIL", 55, "e")]

TIPS = {
    "ssid": "ネットワーク名。\n"
            "AP が名前を伏せている場合は (非公開) と表示します。",
    "bssid": "AP の無線 1 つごとに割り当てられた MAC アドレス。\n"
             "同じ SSID でもバンドごとに別の BSSID になるため、\n"
             "同名の行が複数並ぶことがあります。",
    "band": "周波数帯。中心周波数から算出しています。\n"
            "2.4GHz は電波が遠くまで届く代わりに混みやすく、\n"
            "5GHz / 6GHz は届きにくい代わりに空いています。",
    "ch": "チャネル番号。中心周波数から算出しています。\n"
          "2.4GHz では隣接チャネルが互いに重なるため、\n"
          "1 / 6 / 11 以外を使うと干渉が増えます。",
    "phy": "対応している無線規格。\n"
           "11a/b/g → 11n → 11ac → 11ax (Wi-Fi 6) → 11be (Wi-Fi 7)\n"
           "の順に新しく、新しいほど高速です。",
    "sig": "信号品質 (0〜100%)。\n"
           "Windows が RSSI から算出した値で、\n"
           "Wi-Fi 一覧のアンテナ本数と同じ指標です。",
    "rssi": "受信信号強度 (dBm)。0 に近いほど強い生の値です。\n"
            "-50 前後: 非常に良好   -60〜-70: 良好\n"
            "-70〜-80: やや弱い    -80 以下: 速度が出にくい",
    "sta": "その BSSID に接続しているクライアント台数。\n"
           "AP が広告する BSS Load 要素から取得しています。\n"
           "SSID 単位ではなく、そのバンドの無線単位の値です。\n"
           "「-」は AP が BSS Load を広告していないことを示します。",
    "util": "チャネル使用率。AP がそのチャネルを\n"
            "「使用中」と判定した時間の割合です。\n"
            "自分のネットワークだけでなく、同じチャネルを使う\n"
            "他人の AP の電波も含まれます。\n\n"
            "〜40%: 余裕あり (緑)\n"
            "40〜70%: 混雑 (黄)\n"
            "70%〜: 飽和。リンク速度が高くても実効速度が出ない (赤)",
}


class HeaderTooltip:
    """Treeview の列ヘッダにマウスを乗せたとき、説明を吹き出しで出す。

    ヘッダは独立したウィジェットではないので、<Motion> の座標から
    identify_region / identify_column で対象列を判定する。
    """

    def __init__(self, tree, tips, font, wraplength, delay=400):
        self.tree, self.tips = tree, tips
        self.font, self.wraplength, self.delay = font, wraplength, delay
        self.tip = self.after_id = self.current = None
        tree.bind("<Motion>", self._motion, add="+")
        tree.bind("<Leave>", lambda e: self._hide(), add="+")
        tree.bind("<Button-1>", lambda e: self._hide(), add="+")

    def _motion(self, ev):
        if self.tree.identify_region(ev.x, ev.y) != "heading":
            self._hide()
            return
        col = self.tree.identify_column(ev.x)          # '#1' 形式
        try:
            key = self.tree["columns"][int(col[1:]) - 1]
        except (ValueError, IndexError):
            self._hide()
            return
        if key == self.current and (self.tip or self.after_id):
            return                                     # 同じ列の上で動いただけ
        self._hide()
        self.current = key
        text = self.tips.get(key)
        if text:
            x, y = ev.x_root, ev.y_root
            self.after_id = self.tree.after(self.delay, lambda: self._show(text, x, y))

    def _show(self, text, x, y):
        self.after_id = None
        if self.tip:
            return
        self.tip = tw = tk.Toplevel(self.tree)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=text, justify="left", font=self.font,
                 background="#fffbe6", foreground="#222",
                 relief="solid", borderwidth=1,
                 padx=10, pady=7, wraplength=self.wraplength).pack()
        tw.update_idletasks()
        sw, sh = tw.winfo_screenwidth(), tw.winfo_screenheight()
        w, h = tw.winfo_width(), tw.winfo_height()
        tw.wm_geometry(f"+{min(x + 16, sw - w - 8)}+{min(y + 22, sh - h - 8)}")

    def _hide(self):
        if self.after_id:
            self.tree.after_cancel(self.after_id)
            self.after_id = None
        if self.tip:
            self.tip.destroy()
            self.tip = None
        self.current = None


def pick_font():
    avail = set(tkfont.families())
    for name in ("游ゴシック", "Yu Gothic UI", "Meiryo", "BIZ UDGothic", "MS Gothic"):
        if name in avail:
            return name
    return "TkDefaultFont"


class App(tk.Tk):
    def __init__(self, dpi_mode="?"):
        super().__init__()
        self.title("Wi-Fi スキャナ")

        # 物理 DPI に合わせる。96dpi = 100% が基準。
        dpi = self.winfo_fpixels("1i")
        self.scale = dpi / 96.0
        self.tk.call("tk", "scaling", dpi / 72.0)   # ポイント指定フォントの換算用
        self.dpi_mode, self.dpi = dpi_mode, dpi

        px = lambda n: int(n * self.scale)          # noqa: E731
        self.px = px
        self.geometry(f"{px(980)}x{px(620)}")
        self.minsize(px(760), px(440))

        fam = pick_font()
        self.f_ui = (fam, 10)
        self.f_log = (fam, 9)
        self.option_add("*Font", self.f_ui)

        self.q = queue.Queue()
        self.rows = []
        self.conn = None
        self.sort_key = "sig"
        self.sort_desc = True
        self.busy = False
        self.regsub = None      # ドライバ設定のレジストリサブキー
        self.ifdesc = ""        # インターフェース説明 (PowerShell に渡す)
        self.wmode = None       # 現在の WirelessMode

        self._build()
        self.log(f"起動しました。DPI={dpi:.0f} ({self.scale*100:.0f}%) / 認識モード={dpi_mode}")
        self.log("[スキャン] を押すか、自動更新を有効にしてください。")
        self._log_interfaces()
        self._refresh_band_state()
        self.after(50, self._apply_icon)
        self.after(300, self.start_scan)
        self.after(100, self._drain)

    # -- layout -------------------------------------------------------------
    def _build(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")

        self.btn = ttk.Button(bar, text="スキャン", command=self.start_scan)
        self.btn.pack(side="left")

        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="自動更新", variable=self.auto,
                        command=self._auto_changed).pack(side="left", padx=(12, 4))
        self.interval = tk.IntVar(value=30)
        ttk.Spinbox(bar, from_=10, to=600, increment=10, width=5,
                    textvariable=self.interval).pack(side="left")
        ttk.Label(bar, text="秒").pack(side="left", padx=(2, 0))

        ttk.Button(bar, text="クリップボードにコピー",
                   command=self.copy_clipboard).pack(side="right")
        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y",
                                                   padx=self.px(10))
        self.band_btn = ttk.Button(bar, text="…", command=self._toggle_band)
        self.band_btn.pack(side="right")
        self.band_lbl = ttk.Label(bar, text="2.4GHz: 確認中…")
        self.band_lbl.pack(side="right", padx=(0, self.px(8)))

        self.status = ttk.Label(self, padding=(10, 2), anchor="w", foreground="#0a4")
        self.status.pack(fill="x")

        mid = ttk.Frame(self, padding=(8, 4))
        mid.pack(fill="both", expand=True)

        style = ttk.Style()
        style.configure("Treeview", rowheight=self.px(24))

        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLS],
                                 show="headings", selectmode="browse")
        for key, label, width, anchor in COLS:
            self.tree.heading(key, text=label,
                              command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=self.px(width), anchor=anchor,
                             stretch=(key == "ssid"))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        HeaderTooltip(self.tree, TIPS, self.f_log, self.px(360))

        self.tree.tag_configure("free", background="#e8f6e8")
        self.tree.tag_configure("busy", background="#fdf3dc")
        self.tree.tag_configure("jam", background="#fbe3e3")
        self.tree.tag_configure("me", font=(self.f_ui[0], 10, "bold"))

        ttk.Label(self, text="ログ", padding=(10, 2), anchor="w").pack(fill="x")
        wrap = ttk.Frame(self, padding=(8, 0, 8, 8))
        wrap.pack(fill="x")
        self.logbox = tk.Text(wrap, height=7, wrap="none", font=self.f_log,
                              background="#1e1e1e", foreground="#d4d4d4",
                              insertbackground="#d4d4d4")
        lsb = ttk.Scrollbar(wrap, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=lsb.set, state="disabled")
        self.logbox.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    # -- helpers ------------------------------------------------------------
    def log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"[{ts}] {msg}\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _log_interfaces(self):
        """どのインターフェースを使うか起動時に記録する。障害切り分け用。"""
        try:
            h = open_handle()
            try:
                items = list_interfaces(h)
            finally:
                wlan.WlanCloseHandle(h, None)
        except OSError as exc:
            self.log(f"インターフェースを列挙できません: {exc}")
            return
        chosen = pick_interface(items)
        self.log(f"無線インターフェース {len(items)} 件:")
        for guid, desc, st in items:
            mark = "→" if desc == chosen[1] else " "
            self.log(f"   {mark} {desc} [{IFACE_STATE.get(st, st)}]")

    def _apply_icon(self):
        """ウィンドウとタスクバーのアイコンを差し替える。初回だけ生成に時間がかかる。"""
        try:
            t0 = time.time()
            path = ensure_icon()
            self.iconbitmap(default=path)
            self.log(f"アイコンを適用しました ({time.time() - t0:.1f} 秒)")
        except Exception as exc:                                  # noqa: BLE001
            self.log(f"アイコンを適用できませんでした: {exc}")

    # -- 2.4GHz の有効 / 無効 ------------------------------------------------
    def _refresh_band_state(self, announce=True):
        """ドライバの WirelessMode をレジストリから読んで表示を更新する（管理者権限不要）。"""
        if self.regsub is None:
            try:
                guid_text, self.ifdesc = interface_info()
                self.regsub = find_adapter_regkey(guid_text)
            except OSError as exc:
                self.log(f"バンド設定を読めませんでした: {exc}")
        if self.regsub is None:
            self.band_lbl.configure(text="2.4GHz: 不明", foreground="#888")
            self.band_btn.configure(state="disabled", text="切り替え不可")
            if announce:
                self.log("ドライバ設定が見つからないため 2.4GHz の切り替えは使えません")
            return

        mode = read_wireless_mode(self.regsub)
        changed = (mode != self.wmode)
        self.wmode = mode
        on = (mode != MODE_5GHZ)        # 未設定 (None) はドライバ既定＝全バンド有効
        suffix = " (既定)" if mode is None else ""
        self.band_lbl.configure(text=f"2.4GHz: {'有効' if on else '無効'}{suffix}",
                                foreground="#0a4" if on else "#c00")
        self.band_btn.configure(state="normal",
                                text="2.4GHz を無効化" if on else "2.4GHz を有効化")
        if announce or changed:
            name = WMODE.get(mode, "ドライバ既定" if mode is None else "不明")
            self.log(f"WirelessMode = {mode} : {name}")

    def _toggle_band(self):
        on = (self.wmode != MODE_5GHZ)
        target = MODE_5GHZ if on else MODE_DUAL
        what = "無効化" if on else "有効化"
        warn = "・2.4GHz 専用の SSID には接続できなくなります\n" if on else ""
        if not messagebox.askokcancel(
                f"2.4GHz を{what}",
                f"ドライバ設定 WirelessMode を {target}\n"
                f"（{WMODE.get(target)}）に変更します。\n\n"
                "・管理者権限が必要なため UAC の確認が出ます\n"
                "・アダプタが再起動するため Wi-Fi が一瞬切断されます\n"
                f"{warn}\n続行しますか？",
                parent=self):
            self.log("バンド切り替えをキャンセルしました")
            return

        if set_wireless_mode_elevated(self.ifdesc, target):
            self.log(f"WirelessMode を {target} に変更する要求を送りました（UAC 承認待ち）")
            self.band_btn.configure(state="disabled", text="適用中…")
            for d in (2500, 5000, 8000, 11000):
                self.after(d, lambda: self._refresh_band_state(announce=False))
            self.after(12000, self._after_band_change)
        else:
            self.log("UAC が拒否されたため変更していません")

    def _after_band_change(self):
        self._refresh_band_state()
        self.log("設定変更を反映するため再スキャンします")
        self.start_scan()

    def _auto_changed(self):
        if self.auto.get():
            self.log(f"自動更新を有効にしました ({self.interval.get()} 秒間隔)")
            self._schedule()
        else:
            self.log("自動更新を無効にしました")

    def _schedule(self):
        if self.auto.get():
            self.after(max(10, self.interval.get()) * 1000, self._tick)

    def _tick(self):
        if self.auto.get():
            self.start_scan()

    # -- scanning -----------------------------------------------------------
    def start_scan(self):
        if self.busy:
            return
        self.busy = True
        self.btn.configure(state="disabled", text="スキャン中…")
        self.status.configure(text="スキャン実行中 (WlanScan)…", foreground="#a60")
        self.log("WlanScan を発行しました。ビーコン受信を待っています…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        t0 = time.time()
        try:
            rows, conn, desc, warn = scan()
            self.q.put(("ok", rows, conn, desc, warn, time.time() - t0))
        except Exception as exc:                                  # noqa: BLE001
            self.q.put(("err", exc))

    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "ok":
                    _, rows, conn, desc, warn, dt = item
                    self.rows, self.conn = rows, conn
                    if warn:
                        self.log(warn)
                    note = ("  ※2.4GHz は無効のため 5/6GHz のみ表示しています"
                            if self.wmode == MODE_5GHZ else "")
                    self.log(f"{len(rows)} 件取得しました ({dt:.1f} 秒) / {desc}{note}")
                    self.refresh()
                else:
                    self.log(f"エラー: {item[1]}")
                    self.status.configure(text=f"エラー: {item[1]}", foreground="#c00")
                self.busy = False
                self.btn.configure(state="normal", text="スキャン")
                self._schedule()
        except queue.Empty:
            pass
        self.after(150, self._drain)

    # -- rendering ----------------------------------------------------------
    def sort_by(self, key):
        if key == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = key
            self.sort_desc = key in ("sig", "rssi", "sta", "util")
        self.refresh()

    def refresh(self):
        rows = sorted(self.rows, key=lambda r: r[self.sort_key],
                      reverse=self.sort_desc)
        self.tree.delete(*self.tree.get_children())
        mine = self.conn["bssid"] if self.conn else None
        for r in rows:
            util = "-" if r["util"] < 0 else f"{r['util']}%"
            sta = "-" if r["sta"] < 0 else str(r["sta"])
            tags = []
            if r["util"] >= 0:
                tags.append("jam" if r["util"] >= 70
                            else "busy" if r["util"] >= 40 else "free")
            if r["bssid"] == mine:
                tags.append("me")
            self.tree.insert("", "end", tags=tags, values=(
                r["ssid"], r["bssid"], r["band"], f"ch{r['ch']}", r["phy"],
                f"{r['sig']}%", f"{r['rssi']}dBm", sta, util))

        for key, label, _, _ in COLS:
            mark = ("  ▼" if self.sort_desc else "  ▲") if key == self.sort_key else ""
            self.tree.heading(key, text=label + mark)

        if self.conn and self.conn["state"] == 1:
            c = self.conn
            self.status.configure(
                text=(f"接続中: {c['ssid']}  [{c['bssid']}]  {c['phy']}  "
                      f"受信 {c['rx']} Mbps / 送信 {c['tx']} Mbps  電波 {c['quality']}%"),
                foreground="#0a4")
        else:
            self.status.configure(text="未接続", foreground="#c00")

    def copy_clipboard(self):
        lines = ["\t".join(c[1] for c in COLS)]
        for iid in self.tree.get_children():
            lines.append("\t".join(str(v) for v in self.tree.item(iid, "values")))
        if self.conn and self.conn["state"] == 1:
            c = self.conn
            lines.append("")
            lines.append(f"# 接続中: {c['ssid']} [{c['bssid']}] {c['phy']} "
                         f"RX {c['rx']} Mbps / TX {c['tx']} Mbps / 電波 {c['quality']}%")
        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log(f"クリップボードに {len(self.tree.get_children())} 行コピーしました")


if __name__ == "__main__":
    mode = enable_dpi_awareness()   # 必ず Tk() より前
    try:
        # これを設定しないと pythonw.exe としてタスクバーにまとめられ、
        # こちらのアイコンが使われない。
        C.windll.shell32.SetCurrentProcessExplicitAppUserModelID("wifiscan.tk.app")
    except Exception:                                             # noqa: BLE001
        pass
    App(mode).mainloop()
