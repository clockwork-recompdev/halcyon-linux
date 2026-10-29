"""Machine facts for the gadget API: CPU, memory, battery, Wi-Fi, time zones."""
import datetime
import re
import os
import subprocess
import threading
import time
import zoneinfo
from pathlib import Path


class CpuSampler:
    """Per-core usage, sampled once a second in the background (gadgets poll
    usagePercentage synchronously and expect an instant answer)."""

    def __init__(self):
        self.usage = []
        self._prev = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _read():
        out = []
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3].isdigit():
                    v = [int(x) for x in line.split()[1:]]
                    idle = v[3] + (v[4] if len(v) > 4 else 0)
                    out.append((idle, sum(v)))
        return out

    def _run(self):
        while True:
            try:
                cur = self._read()
                if self._prev and len(self._prev) == len(cur):
                    u = []
                    for (i0, t0), (i1, t1) in zip(self._prev, cur):
                        dt = t1 - t0
                        u.append(round(100 * (1 - (i1 - i0) / dt)) if dt else 0)
                    with self._lock:
                        self.usage = u
                self._prev = cur
            except OSError:
                pass
            time.sleep(1)

    def get(self):
        with self._lock:
            return list(self.usage) or [0] * (os.cpu_count() or 1)


CPU = CpuSampler()


def _meminfo():
    m = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0])
    except OSError:
        pass
    return m


def _cpu_info():
    name, mhz = "", 0
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name") and not name:
                name = line.split(":", 1)[1].strip()
            elif line.startswith("cpu MHz"):
                mhz = max(mhz, int(float(line.split(":", 1)[1])))
    except (OSError, ValueError):
        pass
    try:
        mhz = int(Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq").read_text()) // 1000
    except (OSError, ValueError):
        pass
    return name, mhz


CPU_NAME, CPU_MHZ = _cpu_info()


def machine():
    m = _meminfo()
    arch = os.uname().machine
    return {
        "cpus": [{"name": CPU_NAME, "usagePercentage": u} for u in CPU.get()],
        "totalMemory": m.get("MemTotal", 0) // 1024,                  # MB, like Windows
        "availableMemory": m.get("MemAvailable", m.get("MemFree", 0)) // 1024,
        "processorArchitecture": {"x86_64": "x64", "aarch64": "ARM64"}.get(arch, arch),
        "processorSpeed": CPU_MHZ,
        "processorName": CPU_NAME,
    }


def power():
    bats = sorted(Path("/sys/class/power_supply").glob("BAT*"))
    online = True
    for ac in Path("/sys/class/power_supply").glob("*"):
        try:
            if (ac / "type").read_text().strip() == "Mains":
                online = (ac / "online").read_text().strip() == "1"
        except OSError:
            pass
    if not bats:
        return {"batteryPercentRemaining": 100, "batteryCapacityRemaining": 0, "batteryCapacityTotal": 0,
                "isBatteryCharging": False, "isPowerLineConnected": True, "batteryStatus": 128,  # 128 = no battery
                "timeRemaining": -1, "totalBatteryLifeTime": -1}
    b = bats[0]

    def r(name, default=""):
        try:
            return (b / name).read_text().strip()
        except OSError:
            return default
    pct = int(r("capacity", "100") or 100)
    status = r("status")
    charging = status == "Charging"
    now = int(r("energy_now", r("charge_now", "0")) or 0)
    full = int(r("energy_full", r("charge_full", "0")) or 0)
    rate = int(r("power_now", r("current_now", "0")) or 0)
    remaining = int(now / rate * 3600) if rate and not charging and not online else -1
    flags = (8 if charging else 0) | (1 if pct > 66 else 2 if pct > 33 else 4)
    return {"batteryPercentRemaining": pct, "batteryCapacityRemaining": now // 1000,
            "batteryCapacityTotal": full // 1000, "isBatteryCharging": charging,
            "isPowerLineConnected": online, "batteryStatus": flags, "timeRemaining": remaining,
            "totalBatteryLifeTime": -1}


def wireless():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            parts = line.rsplit(":", 1)
            if line.startswith("yes:") and len(parts) == 2:
                return {"ssid": parts[0][4:].replace("\\:", ":"), "signalStrength": int(parts[1] or 0)}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {"ssid": "", "signalStrength": 0}


# --------------------------------------------------------------- time zones ---
def _local_zone():
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz:
        return tz
    try:
        target = os.readlink("/etc/localtime")
        return target.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def zone_info(name):
    """A time zone the way Windows describes it: bias = minutes to add to local time to get UTC."""
    try:
        z = zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        z = zoneinfo.ZoneInfo("UTC")
        name = "UTC"
    now = datetime.datetime.now(z)
    year = now.year
    jan = datetime.datetime(year, 1, 1, 12, tzinfo=z).utcoffset()
    jul = datetime.datetime(year, 7, 1, 12, tzinfo=z).utcoffset()
    std = min(jan, jul)
    dst = max(jan, jul)
    off = now.utcoffset()
    sign = "+" if off >= datetime.timedelta(0) else "-"
    mins = abs(int(off.total_seconds() // 60))
    city = name.split("/")[-1].replace("_", " ")
    return {
        "name": name,
        "displayName": f"(UTC{sign}{mins // 60:02d}:{mins % 60:02d}) {city}",
        "standardDisplayName": now.tzname() or name,
        "daylightDisplayName": now.tzname() or name,
        "bias": -int(off.total_seconds() // 60),
        "standardBias": 0,
        "daylightBias": -int((dst - std).total_seconds() // 60),
    }


_ZONES = None


def zones():
    global _ZONES
    if _ZONES is None:
        names = sorted(n for n in zoneinfo.available_timezones()
                       if "/" in n and not n.startswith(("Etc/", "SystemV/", "US/", "posix/", "right/")))
        infos = [zone_info(n) for n in names + ["UTC"]]
        infos.sort(key=lambda z: (-z["bias"], z["displayName"]))
        _ZONES = infos
    return _ZONES


def current_zone():
    return zone_info(_local_zone())


def local_time(name):
    """Wall-clock time in a zone, as a string JavaScript's Date.parse accepts."""
    try:
        z = zoneinfo.ZoneInfo(name or _local_zone())
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        z = zoneinfo.ZoneInfo("UTC")
    return datetime.datetime.now(z).strftime("%m/%d/%Y %H:%M:%S")


# --------------------------------------------------------------- folders ---
def xdg_dir(kind):
    """A user folder (PICTURES, MUSIC...). xdg-user-dir answers $HOME when the
    folder isn't configured; never hand a gadget the whole home folder for that."""
    home = str(Path.home())
    try:
        out = subprocess.run(["xdg-user-dir", kind], capture_output=True, text=True, timeout=2).stdout.strip()
        if out and (out.rstrip("/") != home.rstrip("/") or kind == "DESKTOP"):
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    name = {"DOWNLOAD": "Downloads"}.get(kind, kind.capitalize())
    return str(Path.home() / name)


KNOWN = {
    "pictures": "PICTURES", "mypictures": "PICTURES", "samplepictures": "PICTURES", "publicpictures": "PICTURES",
    "music": "MUSIC", "mymusic": "MUSIC", "videos": "VIDEOS", "myvideos": "VIDEOS", "documents": "DOCUMENTS",
    "personal": "DOCUMENTS", "mydocuments": "DOCUMENTS", "downloads": "DOWNLOAD", "desktop": "DESKTOP",
}


# Known-folder GUIDs (FOLDERID_*) Windows gadgets pass instead of names.
GUIDS = {
    "33e28130-4e1e-4676-835a-98395c3bc3bb": "pictures", "a990ae9f-a03b-4e80-94bc-9912d7504104": "pictures",
    "c4900540-2379-4c75-844b-64e6faf8716b": "pictures", "b6ebfb86-6907-413c-9af7-4fc2abf07cc5": "pictures",
    "4bd8d571-6d19-48d3-be97-422220080e43": "music", "2112ab0a-c86a-4ffe-a368-0de96e47012e": "music",
    "18989b1d-99b5-455b-841c-ab7c74e4ddfc": "videos", "491e922f-5643-4af4-a7eb-4e7a138d8174": "videos",
    "fdd39ad0-238f-46af-adb4-6c85480369c7": "documents", "7b0db17d-9cd2-4a93-9733-46cc89022e7c": "documents",
    "374de290-123f-4565-9164-39c4925e467b": "downloads", "b4bfcc3a-db2c-424c-b029-7fe99a87c641": "desktop",
    "5e6c858f-0e22-4760-9afe-ea3317b67173": "profile",
}


# Windows' per-user AppData, kept apart from the real ~/.config.
GADGET_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "halcyon" / "gadget-data"
GUIDS.update({"3eb685db-65f9-4cf6-a03a-e3ef65729f3d": "appdata", "f1b32785-6fba-4fcf-9d55-7b8e7f157091": "localappdata"})


def appdata(rest=""):
    p = GADGET_DATA / "AppData" / rest
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def known_folder(name):
    key = str(name).lower().strip("{} ").replace(" ", "")
    key = GUIDS.get(key, key)
    if key in ("appdata", "roamingappdata"):
        return appdata("Roaming")
    if key in ("localappdata", "local"):
        return appdata("Local")
    if key in ("profile", "userprofile", "home"):
        return str(Path.home())
    if key in KNOWN:
        return xdg_dir(KNOWN[key])
    return ""


def trash():
    files = Path.home() / ".local/share/Trash/files"
    count = size = 0
    if files.is_dir():
        for p in files.iterdir():
            count += 1
            try:
                if p.is_dir():
                    size += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                else:
                    size += p.stat().st_size
            except OSError:
                pass
    return {"fileCount": count, "folderCount": 0, "folderSize": size}


# ------------------------------------------------------------ drives ---
def drives():
    """Mounted filesystems as Windows drive letters: / is C:, then removable and
    other disks from D: on (gadgets show and open them by letter)."""
    out = [("C", "/")]
    try:
        lines = open("/proc/self/mounts").read().splitlines()
    except OSError:
        lines = []
    devs = {l.split()[0] for l in lines if len(l.split()) > 1 and l.split()[1] == "/"}
    for line in lines:
        dev, mnt, fs = (line.split() + ["", "", ""])[:3]
        mnt = mnt.replace("\\040", " ")
        # One letter per disk: /home on the root disk is still C:.
        if not dev.startswith("/dev/") or dev in devs or fs in ("squashfs", "swap") or \
                not (mnt.startswith(("/run/media/", "/media/", "/mnt/")) or mnt in ("/home",)):
            continue
        devs.add(dev)
        out.append((chr(ord("C") + len(out)), mnt))
        if len(out) >= 24:
            break
    return out


def drive_path(letter):
    for l, m in drives():
        if l == letter.upper():
            return m
    return None


# -------------------------------------------------------------- WMI ---
class _Rates:
    """Counter -> per-second rate between successive queries."""
    def __init__(self):
        self.prev = {}

    def rate(self, key, value):
        now = time.monotonic()
        t, v = self.prev.get(key, (now, value))
        self.prev[key] = (now, value)
        return max(0, (value - v) / (now - t)) if now > t else 0


_RATES = _Rates()


def _disk_rows():
    rows, totals = [], {"r": 0, "w": 0, "busy": 0}
    try:
        for line in open("/proc/diskstats"):
            f = line.split()
            name = f[2]
            if re.match(r"^(loop|ram|zram|dm-|sr)", name) or re.match(r"^(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|mmcblk\d+)p?\d+$", name):
                continue
            r, w, busy = int(f[5]) * 512, int(f[9]) * 512, int(f[12])
            rr, wr, br = _RATES.rate(("dr", name), r), _RATES.rate(("dw", name), w), _RATES.rate(("db", name), busy)
            pct = min(100, round(br / 10))           # ms busy per second -> %
            rows.append({"Name": f"{len(rows)} {name}", "DiskReadBytesPersec": int(rr), "DiskWriteBytesPersec": int(wr),
                         "DiskBytesPersec": int(rr + wr), "PercentDiskTime": pct, "PercentIdleTime": 100 - pct,
                         "PercentDiskReadTime": pct, "PercentDiskWriteTime": 0, "CurrentDiskQueueLength": int(f[11])})
            totals["r"] += rr; totals["w"] += wr; totals["busy"] = max(totals["busy"], pct)
    except (OSError, IndexError, ValueError):
        pass
    rows.append({"Name": "_Total", "DiskReadBytesPersec": int(totals["r"]), "DiskWriteBytesPersec": int(totals["w"]),
                 "DiskBytesPersec": int(totals["r"] + totals["w"]), "PercentDiskTime": totals["busy"],
                 "PercentIdleTime": 100 - totals["busy"], "PercentDiskReadTime": totals["busy"],
                 "PercentDiskWriteTime": 0, "CurrentDiskQueueLength": 0})
    return rows


def _net_rows(raw=False):
    rows = []
    try:
        for line in open("/proc/net/dev").read().splitlines()[2:]:
            name, data = line.split(":", 1)
            name = name.strip()
            if name == "lo":
                continue
            f = data.split()
            rx, tx = int(f[0]), int(f[8])
            speed = 0
            try:
                speed = max(0, int(open(f"/sys/class/net/{name}/speed").read())) * 1_000_000
            except (OSError, ValueError):
                pass
            r = rx if raw else int(_RATES.rate(("nr", name), rx))
            t = tx if raw else int(_RATES.rate(("nt", name), tx))
            rows.append({"Name": name, "BytesReceivedPersec": r, "BytesSentPersec": t, "BytesTotalPersec": r + t,
                         "CurrentBandwidth": speed or 1_000_000_000, "PacketsPersec": 0, "Timestamp_Sys100NS": int(time.time() * 1e7),
                         "Frequency_Sys100NS": 10_000_000})
    except (OSError, IndexError, ValueError):
        pass
    return rows


def _processes():
    rows = []
    ncpu = os.cpu_count() or 1
    tick = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            stat = open(f"/proc/{pid}/stat").read()
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            f = stat[stat.rindex(")") + 2:].split()
            cpu = (int(f[11]) + int(f[12])) / tick
            rss = int(f[21]) * page
        except (OSError, ValueError, IndexError):
            continue
        pct = _RATES.rate(("p", pid), cpu) * 100 / ncpu
        rows.append({"Name": name, "IDProcess": int(pid), "ProcessId": int(pid), "PercentProcessorTime": round(pct),
                     "WorkingSet": rss, "WorkingSetSize": rss, "PrivateBytes": rss, "Caption": name})
    return rows


def wmi(cls):
    """Rows for the WMI classes desktop gadgets query, with Windows' field names."""
    c = cls.lower()
    m = _meminfo()
    if c in ("win32_processor",):
        u = CPU.get()
        return [{"Name": CPU_NAME, "DeviceID": "CPU0", "LoadPercentage": round(sum(u) / len(u)) if u else 0,
                 "MaxClockSpeed": CPU_MHZ, "CurrentClockSpeed": CPU_MHZ, "NumberOfCores": os.cpu_count() or 1,
                 "NumberOfLogicalProcessors": os.cpu_count() or 1, "Manufacturer": "", "Architecture": 9}]
    if c in ("win32_perfformatteddata_perfos_processor", "win32_perfformatteddata_counters_processorinformation"):
        u = CPU.get()
        rows = [{"Name": str(i), "PercentProcessorTime": v, "PercentIdleTime": 100 - v, "PercentUserTime": v,
                 "PercentPrivilegedTime": 0, "PercentInterruptTime": 0} for i, v in enumerate(u)]
        t = round(sum(u) / len(u)) if u else 0
        rows.append({"Name": "_Total", "PercentProcessorTime": t, "PercentIdleTime": 100 - t, "PercentUserTime": t,
                     "PercentPrivilegedTime": 0, "PercentInterruptTime": 0})
        return rows
    if c == "win32_operatingsystem":
        try:
            up = float(open("/proc/uptime").read().split()[0])
        except (OSError, ValueError):
            up = 0
        boot = time.strftime("%Y%m%d%H%M%S.000000+000", time.localtime(time.time() - up))
        return [{"Caption": "Halcyon Linux", "Version": "6.1.7601", "BuildNumber": "7601", "OSArchitecture": "64-bit",
                 "TotalVisibleMemorySize": m.get("MemTotal", 0), "FreePhysicalMemory": m.get("MemAvailable", 0),
                 "TotalVirtualMemorySize": m.get("MemTotal", 0) + m.get("SwapTotal", 0),
                 "FreeVirtualMemory": m.get("MemAvailable", 0) + m.get("SwapFree", 0), "LastBootUpTime": boot,
                 "LocalDateTime": time.strftime("%Y%m%d%H%M%S.000000+000"), "CSName": os.uname().nodename,
                 "NumberOfProcesses": len([p for p in os.listdir("/proc") if p.isdigit()])}]
    if c == "win32_computersystem":
        return [{"Name": os.uname().nodename, "TotalPhysicalMemory": m.get("MemTotal", 0) * 1024,
                 "NumberOfProcessors": 1, "NumberOfLogicalProcessors": os.cpu_count() or 1, "Model": "", "Manufacturer": "",
                 "UserName": os.environ.get("USER", "")}]
    if c in ("win32_perfformatteddata_perfos_memory", "win32_perfrawdata_perfos_memory"):
        return [{"AvailableMBytes": m.get("MemAvailable", 0) // 1024, "AvailableKBytes": m.get("MemAvailable", 0),
                 "AvailableBytes": m.get("MemAvailable", 0) * 1024, "CommittedBytes": (m.get("MemTotal", 0) - m.get("MemAvailable", 0)) * 1024,
                 "PercentCommittedBytesInUse": round(100 * (1 - m.get("MemAvailable", 0) / max(1, m.get("MemTotal", 1))))}]
    if c in ("win32_logicaldisk", "win32_volume"):
        rows = []
        for letter, mnt in drives():
            try:
                st = os.statvfs(mnt)
            except OSError:
                continue
            removable = mnt.startswith(("/run/media/", "/media/"))
            rows.append({"DeviceID": f"{letter}:", "Name": f"{letter}:", "DriveLetter": f"{letter}:", "Caption": f"{letter}:",
                         "Size": st.f_blocks * st.f_frsize, "Capacity": st.f_blocks * st.f_frsize,
                         "FreeSpace": st.f_bavail * st.f_frsize, "VolumeName": "Halcyon" if mnt == "/" else os.path.basename(mnt),
                         "Label": os.path.basename(mnt) or "Halcyon", "DriveType": 2 if removable else 3,
                         "FileSystem": "NTFS", "MediaType": 12})
        return rows
    if c in ("win32_perfformatteddata_perfdisk_physicaldisk", "win32_perfformatteddata_perfdisk_logicaldisk"):
        rows = _disk_rows()
        if "logical" in c:
            total = rows[-1]
            rows = [dict(total, Name=f"{l}:") for l, _m in drives()] + [total]
        return rows
    if c in ("win32_perfformatteddata_tcpip_networkinterface", "win32_perfformatteddata_tcpip_networkadapter"):
        return _net_rows()
    if c in ("win32_perfrawdata_tcpip_networkinterface", "win32_perfrawdata_tcpip_networkadapter"):
        return _net_rows(raw=True)
    if c in ("win32_networkadapter", "win32_networkadapterconfiguration"):
        rows = []
        for i, r in enumerate(_net_rows(raw=True)):
            n = r["Name"]
            try:
                mac = open(f"/sys/class/net/{n}/address").read().strip().upper()
                up = open(f"/sys/class/net/{n}/operstate").read().strip() == "up"
            except OSError:
                mac, up = "", False
            rows.append({"Name": n, "Description": n, "NetConnectionID": n, "MACAddress": mac, "IPEnabled": up,
                         "NetEnabled": up, "NetConnectionStatus": 2 if up else 7, "Index": i, "InterfaceIndex": i,
                         "Speed": r["CurrentBandwidth"], "IPAddress": [], "AdapterType": "Ethernet 802.3"})
        return rows
    if c in ("win32_perfformatteddata_perfproc_process", "win32_process"):
        rows = _processes()
        if "perfproc" in c:
            u = CPU.get()
            rows.append({"Name": "_Total", "IDProcess": 0, "PercentProcessorTime": round(sum(u) / len(u)) if u else 0,
                         "WorkingSet": 0})
            rows.append({"Name": "Idle", "IDProcess": 0, "PercentProcessorTime": 100 - (round(sum(u) / len(u)) if u else 0),
                         "WorkingSet": 0})
        return rows
    if c == "win32_battery":
        p = power()
        if p["batteryStatus"] == 128:
            return []
        return [{"EstimatedChargeRemaining": p["batteryPercentRemaining"], "BatteryStatus": 6 if p["isBatteryCharging"] else 2 if p["isPowerLineConnected"] else 1,
                 "EstimatedRunTime": p["timeRemaining"] // 60 if p["timeRemaining"] > 0 else 71582788, "Name": "Battery"}]
    if c == "win32_videocontroller":
        name = ""
        try:
            name = subprocess.run(["sh", "-c", "lspci | grep -Ei 'vga|3d' | head -1 | cut -d: -f3"], capture_output=True,
                                  text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        return [{"Name": name or "Display adapter", "Caption": name, "AdapterRAM": 0, "DriverVersion": ""}]
    return []


_WHERE = re.compile(r"""(\w+)\s*(=|<>|!=|>=|<=|>|<|\s+like\s+)\s*('(?:[^']|'')*'|"[^"]*"|[-\d.]+|true|false)""", re.I)


def wmi_query(wql):
    """Run a simple WQL SELECT: field list, one class, WHERE with AND-ed comparisons."""
    m = re.match(r"\s*select\s+(.+?)\s+from\s+(\w+)(?:\s+where\s+(.+))?\s*$", wql, re.I | re.S)
    if not m:
        return []
    rows = wmi(m.group(2))
    where = m.group(3)
    if where:
        conds = _WHERE.findall(where)

        def ok(row):
            low = {k.lower(): v for k, v in row.items()}
            for field, op, val in conds:
                have = low.get(field.lower())
                op = op.strip().lower()
                v = val[1:-1].replace("''", "'") if val[:1] in "'\"" else val
                if op == "like":
                    pat = "^" + re.escape(str(v)).replace("%", ".*").replace("_", ".") + "$"
                    if not re.match(pat, str(have or ""), re.I):
                        return False
                    continue
                try:
                    a, b = float(have), float(v)
                except (TypeError, ValueError):
                    a, b = str(have).lower(), str(v).lower()
                if not {"=": a == b, "<>": a != b, "!=": a != b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]:
                    return False
            return True
        rows = [r for r in rows if ok(r)]
    fields = [f.strip() for f in m.group(1).split(",")]
    if fields != ["*"]:
        keep = {f.lower() for f in fields}
        rows = [{k: v for k, v in r.items() if k.lower() in keep} for r in rows]
    return rows
