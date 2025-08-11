# geochange_app.py
# GeoChange — Emulator Manager + Location Spoofer (PySide6 6.9+)
# Эмулятор запускается в отдельном окне; карта встроена. Клик по сохранённой
# локации автоматически телепортирует устройство.

import os, sys, json, subprocess, threading
from pathlib import Path

from PySide6.QtCore import Qt, QObject, Slot, Signal, QUrl, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTextEdit, QComboBox, QProgressBar, QFrame
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebChannel import QWebChannel

APP_TITLE   = "GeoChange — Emulator + Location Spoofer"
DEFAULT_ROOT= Path(r"C:\geochange")
DEFAULT_SDK = DEFAULT_ROOT / "sdk"
DEFAULT_AVD = DEFAULT_ROOT / "avd"
SYSTEM_IMAGE= "system-images;android-34;google_apis_playstore;x86_64"
AVD_NAME    = "GeoChangePlay"
DEVICE_NAME = "pixel_6"
DATA_FILE   = Path(__file__).with_name("saved_locations.json")

def ensure_dirs():
    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    (DEFAULT_ROOT / "map_cache").mkdir(parents=True, exist_ok=True)

def sdk_env(sdk_dir: Path):
    env = os.environ.copy()
    env["ANDROID_SDK_ROOT"] = str(sdk_dir)
    env["ANDROID_HOME"] = str(sdk_dir)
    env["ANDROID_AVD_HOME"] = str(DEFAULT_AVD)
    env["PATH"] = (
        str(sdk_dir / "platform-tools") + os.pathsep +
        str(sdk_dir / "emulator") + os.pathsep +
        str(sdk_dir / "cmdline-tools" / "latest" / "bin") + os.pathsep +
        env.get("PATH", "")
    )
    return env

def adb_path(sdk_dir: Path):
    cand = sdk_dir / "platform-tools" / "adb.exe"
    return str(cand) if cand.exists() else "adb"

def emulator_path(sdk_dir: Path):
    cand = sdk_dir / "emulator" / "emulator.exe"
    return str(cand) if cand.exists() else "emulator"

def run_popen(cmd, env=None):
    return subprocess.Popen(cmd, env=env, text=True, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def run_check(cmd, env=None):
    return subprocess.run(cmd, env=env, text=True, capture_output=True)

def load_items():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def save_items(items):
    DATA_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                         encoding="utf-8")

def make_logo_pixmap(size: int = 48) -> QPixmap:
    pm = QPixmap(size, size); pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    p.setBrush(QColor(40,120,255)); p.setPen(Qt.NoPen)
    r = int(size * 0.45)
    p.drawEllipse(int(size/2 - r), int(size/2 - r), 2*r, 2*r)
    p.setPen(Qt.white)
    p.setFont(QFont("Segoe UI", int(size * 0.5), QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, "G")
    p.end()
    return pm

# ---------- Web (Map) Bridge ----------
class Bridge(QObject):
    itemsUpdated = Signal(str)

    def __init__(self, main):
        super().__init__()
        self.main = main

    @Slot()
    def requestItems(self):
        self.itemsUpdated.emit(json.dumps(load_items(), ensure_ascii=False))

    @Slot(str, float, float)
    def saveItem(self, name, lat, lon):
        name = name.strip()
        if not name:
            return
        items = load_items()
        items = [i for i in items if i.get("name") != name]
        items.append({"name": name, "lat": float(lat), "lon": float(lon)})
        save_items(items)
        self.itemsUpdated.emit(json.dumps(items, ensure_ascii=False))

    @Slot(str)
    def deleteItem(self, name):
        items = [i for i in load_items() if i.get("name") != name]
        save_items(items)
        self.itemsUpdated.emit(json.dumps(items, ensure_ascii=False))

    @Slot(float, float, str)
    def teleport(self, lat, lon, target=""):
        sdk = self.main.sdkRoot(); env = sdk_env(sdk)
        if not target.strip():
            target = self.main.adbTarget.text().strip() or "emulator-5554"
        adb = adb_path(sdk)
        # убедимся, что устройство online
        try:
            st = run_check([adb, "-s", target, "get-state"], env=env)
            if (st.stdout or "").strip() != "device":
                subprocess.run([adb, "-s", target, "wait-for-device"], env=env,
                               text=True, timeout=30)
        except Exception:
            pass
        run_check([adb, "-s", target, "emu", "geo", "fix", str(lon), str(lat)],
                  env=env)

# ---------- Embedded HTML (Leaflet map) ----------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>GeoChange — Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  html,body{height:100%;margin:0;background:#0b0f14;color:#e6eefc;
            font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
  #topbar{display:flex;gap:.6rem;padding:.6rem;align-items:center;
          border-bottom:1px solid #1b2230;background:#0f1722;position:sticky;top:0;z-index:10}
  #map{height:calc(100% - 60px)}
  .btn{padding:.55rem .9rem;border:1px solid #24324b;border-radius:.7rem;
       cursor:pointer;background:#152034;color:#e6eefc}
  .btn:hover{background:#1a2842}
  input,select{padding:.5rem .65rem;border:1px solid #24324b;border-radius:.7rem;
       min-width:10rem;background:#0f1722;color:#e6eefc}
  #saved{width:16rem}
  .spacer{flex:1 1 auto}
  #status{font-size:.9rem;opacity:.9}
</style>
</head>
<body>
  <div id="topbar">
    <input id="search" placeholder="Search address/place (Enter)">
    <button class="btn" id="btnSearch">Find</button>
    <button class="btn" id="btnTeleport">Teleport here</button>
    <select id="saved"></select>
    <button class="btn" id="btnSave">Save</button>
    <button class="btn" id="btnDelete">Delete</button>
    <span class="spacer"></span>
    <span id="status"></span>
  </div>
  <div id="map"></div>

<script>
let bridge=null;
new QWebChannel(qt.webChannelTransport, ch=>{
  bridge = ch.objects.bridge;
  bridge.itemsUpdated.connect(items_json=>{
    const items = JSON.parse(items_json);
    const sel = document.getElementById('saved');
    sel.innerHTML = '';
    items.forEach(it=>{
      const opt = document.createElement('option');
      // показываем только имя (без координат)
      opt.value = JSON.stringify(it);
      opt.textContent = it.name;
      sel.appendChild(opt);
    });
  });
  bridge.requestItems();
});

const map = L.map('map',{zoomControl:true}).setView([50.45,30.52],5);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            {maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map);
const marker = L.marker([50.45,30.52],{draggable:true}).addTo(map);

function setStatus(t){ document.getElementById('status').innerText=t||''; }

map.on('click',e=>marker.setLatLng(e.latlng));
marker.on('dragend',()=>{
  const p=marker.getLatLng();
  setStatus(`Selected: ${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`);
});

document.getElementById('btnTeleport').onclick=()=>{
  const p=marker.getLatLng();
  bridge.teleport(p.lat, p.lng, "");
};

document.getElementById('btnSave').onclick=()=>{
  const n = prompt('Name for this location:');
  if(!n) return;
  const p=marker.getLatLng();
  bridge.saveItem(n, p.lat, p.lng);
};

document.getElementById('btnDelete').onclick=()=>{
  const sel=document.getElementById('saved');
  if(!sel.value) return;
  const obj=JSON.parse(sel.value);
  bridge.deleteItem(obj.name);
};

// поиск по Nominatim
document.getElementById('btnSearch').onclick=doSearch;
document.getElementById('search').addEventListener('keydown',e=>{
  if(e.key==='Enter') doSearch();
});
function doSearch(){
  const q=document.getElementById('search').value.trim();
  if(!q) return;
  setStatus('Searching…');
  fetch('https://nominatim.openstreetmap.org/search?format=json&q='+encodeURIComponent(q))
    .then(r=>r.json())
    .then(res=>{
      if(!res.length){ setStatus('Nothing found'); return; }
      const f=res[0], lat=parseFloat(f.lat), lon=parseFloat(f.lon);
      map.setView([lat,lon],13); marker.setLatLng([lat,lon]);
      setStatus(f.display_name);
    }).catch(e=>setStatus('Search error: '+e));
}

// ⚡ Авотелепорт по клику на сохранённую локацию
document.getElementById('saved').addEventListener('change',()=>{
  const sel=document.getElementById('saved'); if(!sel.value) return;
  const obj=JSON.parse(sel.value);
  marker.setLatLng([obj.lat,obj.lon]);
  map.setView([obj.lat,obj.lon],13);
  setStatus(`Selected: ${obj.name}`);
  // сразу отправляем координаты в эмулятор
  bridge.teleport(obj.lat, obj.lon, "");
});
</script>
</body>
</html>
"""

# ---------- Header ----------
class Header(QWidget):
    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(12,10,12,10); h.setSpacing(12)
        logo = QLabel(); logo.setPixmap(make_logo_pixmap(48))
        title = QLabel("GeoChange")
        f = title.font(); f.setPointSize(14); f.setBold(True); title.setFont(f)
        title.setStyleSheet("color:#e6eefc;")
        h.addWidget(logo, 0, Qt.AlignLeft|Qt.AlignVCenter)
        h.addWidget(title, 0, Qt.AlignLeft|Qt.AlignVCenter)
        h.addStretch(1)
        self.setStyleSheet("background:#0f1722; border-bottom:1px solid #1b2230;")

# ---------- Emulator Tab ----------
class EmulatorTab(QWidget):
    devicesListed = Signal(str)
    logSig   = Signal(str)
    busySig  = Signal(bool)
    refreshSig = Signal()

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(12,12,12,12); self.layout().setSpacing(10)

        row1 = QHBoxLayout()
        self.sdkEdit = QLineEdit(str(DEFAULT_SDK))
        row1.addWidget(QLabel("SDK root:")); row1.addWidget(self.sdkEdit, 1)

        row2 = QHBoxLayout()
        self.avdEdit = QLineEdit(AVD_NAME)
        row2.addWidget(QLabel("AVD name:")); row2.addWidget(self.avdEdit, 1)

        self.layout().addLayout(row1); self.layout().addLayout(row2)

        btns = QHBoxLayout()
        self.btnInstall = QPushButton("Install SDK")
        self.btnCreate  = QPushButton("Create AVD (Play)")
        self.btnStart   = QPushButton("Start Emulator")
        self.btnStop    = QPushButton("Stop Emulator")
        self.btnPlay    = QPushButton("Open Play Store")
        self.btnList    = QPushButton("List ADB Devices")
        for b in [self.btnInstall, self.btnCreate, self.btnStart, self.btnStop, self.btnPlay, self.btnList]:
            b.setCursor(Qt.PointingHandCursor); btns.addWidget(b)
        self.layout().addLayout(btns)

        devRow = QHBoxLayout()
        self.deviceCombo = QComboBox()
        devRow.addWidget(QLabel("ADB devices:")); devRow.addWidget(self.deviceCombo, 1)
        self.layout().addLayout(devRow)

        self.progress = QProgressBar(); self.progress.setRange(0,0); self.progress.setVisible(False)
        self.layout().addWidget(self.progress)
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setStyleSheet("background:#0b0f14; color:#cfe0ff; border:1px solid #1b2230;")
        self.layout().addWidget(self.log, 1)

        self.btnInstall.clicked.connect(self.install_sdk)
        self.btnCreate.clicked.connect(self.create_avd)
        self.btnStart.clicked.connect(self.start_emulator)
        self.btnStop.clicked.connect(self.stop_emulator)
        self.btnPlay.clicked.connect(self.open_play_store)
        self.btnList.clicked.connect(self.list_devices)

        self.logSig.connect(self._append_log_main)
        self.busySig.connect(self._set_busy_main)
        self.refreshSig.connect(self.refresh_buttons_state)

        self.refresh_buttons_state()

    def sdkRoot(self) -> Path:
        return Path(self.sdkEdit.text().strip())

    # --- thread-safe UI helpers
    def append_log(self, s: str): self.logSig.emit(s)
    def _append_log_main(self, s: str):
        self.log.append(s); self.log.moveCursor(QTextCursor.End)
    def set_busy(self, busy: bool): self.busySig.emit(busy)
    def _set_busy_main(self, busy: bool):
        self.progress.setVisible(busy)
        for w in [self.btnInstall, self.btnCreate, self.btnStart, self.btnStop, self.btnPlay, self.btnList, self.sdkEdit, self.avdEdit]:
            w.setEnabled(not busy)
    def run_threaded(self, fn): threading.Thread(target=fn, daemon=True).start()

    def refresh_buttons_state(self):
        sdk_ok = (self.sdkRoot() / "emulator" / "emulator.exe").exists()
        avd_ok = (DEFAULT_AVD / f"{self.avdEdit.text().strip() or AVD_NAME}.avd").exists()
        self.btnInstall.setEnabled(not sdk_ok)
        self.btnCreate.setEnabled(sdk_ok and not avd_ok)

    # --- wait for device
    def _wait_for_device(self, sdk, serial: str, timeout_sec: int = 120) -> bool:
        env = sdk_env(sdk); adb = adb_path(sdk)
        try:
            subprocess.run([adb, "-s", serial, "wait-for-device"], env=env, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            return False
        try:
            out = run_check([adb, "-s", serial, "get-state"], env=env)
            return (out.stdout or "").strip() == "device"
        except Exception:
            return False

    # --- actions
    def install_sdk(self):
        def work():
            self.set_busy(True); self.append_log("Installing SDK...")
            try:
                url = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
                sdk = self.sdkRoot()
                cmdtools = sdk / "cmdline-tools" / "latest"
                cmdtools.parent.mkdir(parents=True, exist_ok=True)
                sdkmanager = cmdtools / "bin" / "sdkmanager.bat"

                if not sdkmanager.exists():
                    self.append_log("Downloading commandlinetools...")
                    import urllib.request, zipfile, shutil
                    zip_path = sdk / "commandlinetools.zip"
                    with urllib.request.urlopen(url) as r, open(zip_path, "wb") as f: f.write(r.read())
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        tmp = sdk / "_tmp_cmdline"; shutil.rmtree(tmp, ignore_errors=True); z.extractall(tmp)
                        inner = tmp / "cmdline-tools"; shutil.rmtree(cmdtools, ignore_errors=True); shutil.move(str(inner), str(cmdtools))
                        shutil.rmtree(tmp, ignore_errors=True)
                    zip_path.unlink(missing_ok=True)
                    self.append_log("commandlinetools installed.")

                env = sdk_env(sdk)

                # update
                args = [str(sdkmanager), "--sdk_root="+str(sdk), "--update"]
                self.append_log("> " + " ".join(args))
                p = run_popen(args, env=env)
                for line in p.stdout: self.append_log(line.rstrip())

                # accept licenses
                self.append_log("> Accepting licenses...")
                p = subprocess.Popen([str(sdkmanager), "--sdk_root="+str(sdk), "--licenses"],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                try:
                    out,_ = p.communicate(input=("y\n"*50), timeout=240); self.append_log(out or "")
                except subprocess.TimeoutExpired:
                    p.kill(); self.append_log("[!] License acceptance timeout, try again if needed.")

                # install packages
                for pkg in ["platform-tools","emulator",SYSTEM_IMAGE]:
                    args = [str(sdkmanager), "--sdk_root="+str(sdk), pkg]
                    self.append_log("> " + " ".join(args))
                    p = run_popen(args, env=env)
                    for line in p.stdout: self.append_log(line.rstrip())

                self.append_log("SDK install finished.")
            except Exception as e:
                self.append_log(f"[ERROR] {e}")
            finally:
                self.set_busy(False); self.refreshSig.emit()
        self.run_threaded(work)

    def create_avd(self):
        def work():
            self.set_busy(True); self.append_log("Creating AVD...")
            try:
                sdk = self.sdkRoot(); env = sdk_env(sdk)
                avdmanager = sdk / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat"
                args = [str(avdmanager), "--verbose", "create", "avd",
                        "-n", self.avdEdit.text().strip() or AVD_NAME,
                        "-k", SYSTEM_IMAGE, "--device", DEVICE_NAME]
                self.append_log("> " + " ".join(args))
                p = run_popen(args, env=env)
                for line in p.stdout: self.append_log(line.rstrip())
                self.append_log("AVD created.")
            except Exception as e:
                self.append_log(f"[ERROR] {e}")
            finally:
                self.set_busy(False); self.refreshSig.emit()
        self.run_threaded(work)

    def start_emulator(self):
        def work():
            self.set_busy(True); self.append_log("Starting emulator...")
            try:
                sdk = self.sdkRoot(); env = sdk_env(sdk); emu = emulator_path(sdk)
                avd = self.avdEdit.text().strip() or AVD_NAME
                args = [emu, "-avd", avd, "-netdelay", "none", "-netspeed", "full", "-gpu", "host"]
                self.append_log("> " + " ".join(args))
                subprocess.Popen(args, env=env, creationflags=subprocess.DETACHED_PROCESS)
                self.append_log("Start command sent. Waiting for ADB...")
                serial = "emulator-5554"
                if self._wait_for_device(sdk, serial, timeout_sec=180):
                    self.append_log(f"Device {serial} is online.")
                    self.main.adbTarget.setText(serial)
                    self.deviceCombo.clear(); self.deviceCombo.addItem(serial)
                else:
                    self.append_log("[!] Device didn't come online in time. Use 'List ADB Devices' and retry.")
            except Exception as e:
                self.append_log(f"[ERROR] {e}")
            finally:
                self.set_busy(False)
        self.run_threaded(work)

    def stop_emulator(self):
        def work():
            self.set_busy(True); self.append_log("Stopping emulator...")
            try:
                sdk = self.sdkRoot(); env = sdk_env(sdk); adb = adb_path(sdk)
                serial = self.main.adbTarget.text().strip() or "emulator-5554"
                out = run_check([adb, "-s", serial, "emu", "kill"], env=env)
                self.append_log(out.stdout or out.stderr or "Kill sent.")
                try:
                    subprocess.run([adb, "-s", serial, "wait-for-disconnect"], env=env, text=True, timeout=25)
                    self.append_log("Emulator disconnected.")
                except subprocess.TimeoutExpired:
                    self.append_log("[!] Timeout waiting for disconnect. If UI still running, close the window manually.")
            except Exception as e:
                self.append_log(f"[ERROR] {e}")
            finally:
                self.set_busy(False)
        self.run_threaded(work)

    def open_play_store(self):
        def work():
            self.append_log("Opening Play Store...")
            sdk = self.sdkRoot(); env = sdk_env(sdk); adb = adb_path(sdk)
            args = [adb, "shell", "monkey", "-p", "com.android.vending",
                    "-c", "android.intent.category.LAUNCHER", "1"]
            self.append_log("> " + " ".join(args))
            out = run_check(args, env=env)
            self.append_log(out.stdout or out.stderr or "Launched.")
        self.run_threaded(work)

    def list_devices(self):
        def work():
            sdk = self.sdkRoot(); env = sdk_env(sdk); adb = adb_path(sdk)
            out = run_check([adb, "devices"], env=env)
            self.append_log(out.stdout or out.stderr or "")
            self.deviceCombo.clear()
            first = ""
            if out.stdout:
                for line in out.stdout.splitlines():
                    if "\tdevice" in line:
                        dev = line.split("\t")[0]
                        self.deviceCombo.addItem(dev)
                        if not first and dev.startswith("emulator-"):
                            first = dev
            if first:
                self.main.adbTarget.setText(first)
        self.run_threaded(work)

# ---------- Map Tab ----------
class MapTab(QWebEngineView):
    def __init__(self, main):
        super().__init__()
        self.main = main
        profile = QWebEngineProfile.defaultProfile()
        cache_dir = str((DEFAULT_ROOT / "map_cache").resolve())
        profile.setCachePath(cache_dir)
        profile.setPersistentStoragePath(cache_dir)

        self.channel = QWebChannel(self.page())
        self.bridge = Bridge(main)
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)

        self.setHtml(HTML, QUrl("https://local.content/"))

# ---------- Main ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.setWindowTitle(APP_TITLE)
        self.resize(1220, 820)
        self.setStyleSheet("""
            QMainWindow { background:#0b0f14; color:#e6eefc; }
            QLabel, QLineEdit, QComboBox, QTextEdit { color:#e6eefc; }
            QLineEdit, QComboBox { background:#0f1722; border:1px solid #1b2230; border-radius:8px; padding:6px; }
            QPushButton { background:#152034; border:1px solid #24324b; padding:9px 12px; border-radius:10px; color:#e6eefc; }
            QPushButton:hover { background:#1a2842; }
            QTabBar::tab { background:#0f1722; color:#cfe0ff; padding:10px 16px; margin-right:2px; border-top-left-radius:10px; border-top-right-radius:10px; }
            QTabBar::tab:selected { background:#152034; color:#ffffff; border:1px solid #24324b; }
            QProgressBar { background:#0f1722; border:1px solid #1b2230; border-radius:8px; text-align:center; }
            QProgressBar::chunk { background:#2a6bff; }
        """)

        wrapper = QWidget(); v = QVBoxLayout(wrapper); v.setContentsMargins(0,0,0,0); v.setSpacing(0)
        header = Header(); v.addWidget(header, 0)
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setStyleSheet("color:#1b2230;"); v.addWidget(line, 0)

        self.tabs = QTabWidget(); v.addWidget(self.tabs, 1)
        self.emu = EmulatorTab(self); self.tabs.addTab(self.emu, "Emulator Manager")
        self.mapTab = MapTab(self); self.tabs.addTab(self.mapTab, "Location Spoofer")

        # скрытое поле: текущая ADB-цель
        self.adbTarget = QLineEdit(); self.adbTarget.setVisible(False)

        self.setCentralWidget(wrapper)

        # Автосписок устройств после запуска, чтобы target был задан
        QTimer.singleShot(400, self.emu.list_devices)

    def sdkRoot(self) -> Path:
        return self.emu.sdkRoot()

def main():
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
