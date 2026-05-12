import io
import json
import os
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from math import radians, sin, cos, asin, sqrt

st.set_page_config(
    page_title="AAU 동일 SFP 탐색기",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

PRIMARY = "#2DB400"
ORANGE  = "#FF6600"
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(BASE_DIR, "SFP 정보.xlsx")
SHARED_FILE  = os.path.join(BASE_DIR, "_sfp_uploaded.xlsx")

st.markdown("""
<style>
@media (max-width: 768px) {
    div[data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        flex: 1 1 100% !important; min-width: 100% !important;
    }
    .block-container { padding-left: 0.75rem !important; padding-right: 0.75rem !important; }
    .app-title { font-size: 1.25rem !important; }
}
.app-title {
    color: #2DB400;
    font-size: 1.8rem;
    font-weight: 700;
    margin: 0 0 0.5rem 0;
    line-height: 1.3;
}
</style>
""", unsafe_allow_html=True)


# ── 유틸 ─────────────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((radians(lat2 - lat1)) / 2) ** 2 + cos(p1) * cos(p2) * sin((radians(lon2 - lon1)) / 2) ** 2
    return 2 * R * asin(sqrt(a))


def get_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


@st.cache_data(show_spinner="데이터 로딩 중...")
def load_sfp_data(source, _cache_key):
    buf = io.BytesIO(source) if isinstance(source, bytes) else source
    df = pd.read_excel(buf, sheet_name=0)
    cols = df.columns.tolist()
    rename = {
        cols[7]:  "station_name",
        cols[8]:  "lat_deg",  cols[9]:  "lat_min",
        cols[10]: "lat_sec",  cols[11]: "lat_cs",
        cols[12]: "lon_deg",  cols[13]: "lon_min",
        cols[14]: "lon_sec",  cols[15]: "lon_cs",
    }
    df = df.rename(columns=rename)
    coord_cols = ["lat_deg", "lat_min", "lat_sec", "lat_cs",
                  "lon_deg", "lon_min", "lon_sec", "lon_cs"]
    df = df.dropna(subset=coord_cols).copy()
    for c in coord_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=coord_cols)
    df["lat"] = df["lat_deg"] + df["lat_min"] / 60 + (df["lat_sec"] + df["lat_cs"] / 100) / 3600
    df["lon"] = df["lon_deg"] + df["lon_min"] / 60 + (df["lon_sec"] + df["lon_cs"] / 100) / 3600
    df = df[(df["lat"].between(33, 39)) & (df["lon"].between(124, 132))]
    df["_lat_r"] = df["lat"].round(5)
    df["_lon_r"] = df["lon"].round(5)
    return df.reset_index(drop=True)


def sfp_combos_for_station(df, station_name):
    rows = df[df["station_name"] == station_name][["vendor", "vendorprod", "wl"]].dropna().drop_duplicates()
    return rows.reset_index(drop=True)


def nearest_same_sfp(df, vendor, vendorprod, wl, ref_lat, ref_lon, ref_lat_r, ref_lon_r, n):
    matched = df[
        (df["vendor"] == vendor) &
        (df["vendorprod"] == vendorprod) &
        (df["wl"] == wl)
    ].copy()
    site_df = (
        matched.groupby(["_lat_r", "_lon_r"], sort=False)
        .agg(station_name=("station_name", "first"),
             lat=("lat", "first"), lon=("lon", "first"))
        .reset_index()
    )
    site_df = site_df[
        ~((site_df["_lat_r"] == ref_lat_r) & (site_df["_lon_r"] == ref_lon_r))
    ]
    site_df["distance_km"] = site_df.apply(
        lambda r: haversine_km(ref_lat, ref_lon, r["lat"], r["lon"]), axis=1
    )
    return site_df.nsmallest(n, "distance_km").reset_index(drop=True)


def build_result_html(results, vendor, prod, wl):
    rows = ""
    for i, row in results.iterrows():
        rank = i + 1
        rows += (
            "<tr class='mr' onclick='toggle(__R__)'>".replace("__R__", str(rank)) +
            "<td class='rk'>" + str(rank) + "</td>" +
            "<td class='nm'>" + str(row.station_name) + "</td>" +
            "<td class='ds'>" + f"{row.distance_km:.2f}" + " km</td></tr>" +
            "<tr id='d__R__' class='dr'>".replace("__R__", str(rank)) +
            "<td></td><td colspan='2' class='dc'>" +
            str(vendor) + " / " + str(prod) + " / " + str(wl) + " nm" +
            "</td></tr>"
        )

    height = len(results) * 46 + 20
    template = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;font-size:14px;background:transparent;color:#111}
@media(prefers-color-scheme:dark){body{color:#f0f0f0}.ds{color:#aaa!important}.mr{border-bottom-color:rgba(255,255,255,0.12)!important}}
table{width:100%;border-collapse:collapse}
.mr{cursor:pointer;border-bottom:1px solid rgba(128,128,128,0.2)}
.mr:active{background:rgba(128,128,128,0.1)}
.mr td{padding:11px 8px}
.rk{width:36px;text-align:center;color:__ORANGE__;font-weight:700;font-size:13px}
.nm{word-break:break-all;line-height:1.4}
.ds{width:72px;text-align:right;color:#888;white-space:nowrap;font-size:13px}
.dr{display:none}
.dr.open{display:table-row}
.dc{padding:5px 8px 10px 44px;font-size:12px;color:__PRIMARY__;border-bottom:2px solid rgba(45,180,0,0.25)}
</style></head>
<body><table>__ROWS__</table>
<script>
function toggle(r){var d=document.getElementById('d'+r);d.classList.toggle('open');}
</script></body></html>"""

    return (
        template
        .replace("__ROWS__",    rows)
        .replace("__ORANGE__",  ORANGE)
        .replace("__PRIMARY__", PRIMARY),
        height
    )


def build_map_html(ref_lat, ref_lon, ref_name, results, vendor, prod, wl):
    stations_json = json.dumps([
        {
            "rank": i + 1,
            "name": row.station_name,
            "lat":  row.lat,
            "lon":  row.lon,
            "dist": f"{row.distance_km:.2f}",
            "vendor": vendor,
            "prod":   prod,
            "wl":     wl,
        }
        for i, row in results.iterrows()
    ], ensure_ascii=False)

    html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
html, body { width:100%; height:100%; }
#wrap { position:relative; }
#map { width:100%; height:490px; }
#overlay {
    position:absolute; top:0; left:0; width:100%; height:490px;
    background:rgba(0,0,0,0.08); display:flex;
    align-items:center; justify-content:center;
    z-index:1000; cursor:pointer;
}
#overlay span {
    background:rgba(0,0,0,0.55); color:#fff;
    padding:8px 18px; border-radius:20px;
    font-size:13px; pointer-events:none;
}
.num-mk {
    width:30px; height:30px; background:__ORANGE__;
    border-radius:50%; color:#fff; font-weight:bold;
    font-size:13px; display:flex; align-items:center;
    justify-content:center; cursor:pointer;
    box-shadow:0 2px 6px rgba(0,0,0,.4); border:2px solid #fff;
    margin-left:-15px; margin-top:-15px;
}
.ref-mk {
    width:36px; height:36px; background:__PRIMARY__;
    border-radius:50%; color:#fff; font-size:18px;
    display:flex; align-items:center; justify-content:center;
    box-shadow:0 2px 8px rgba(0,0,0,.5); border:3px solid #fff;
    margin-left:-18px; margin-top:-18px;
}
.popup-content { font-size:13px; line-height:1.8;
    font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; min-width:180px; }
.popup-rank { color:__ORANGE__; font-weight:bold; font-size:15px; }
.loc-btn {
    background:#fff; border:2px solid rgba(0,0,0,0.25);
    border-radius:6px; padding:6px 10px; cursor:pointer;
    font-size:13px; font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
    white-space:nowrap; line-height:1;
}
.loc-btn:active { background:#f0f0f0; }
.loc-btn:disabled { opacity:0.6; cursor:wait; }
.my-loc-mk {
    width:16px; height:16px; background:#4285F4;
    border-radius:50%; border:3px solid #fff;
    box-shadow:0 0 0 2px #4285F4;
    animation:pulse 2s infinite;
    margin-left:-8px; margin-top:-8px;
}
@keyframes pulse {
    0%   { box-shadow:0 0 0 0   rgba(66,133,244,0.5); }
    70%  { box-shadow:0 0 0 10px rgba(66,133,244,0);   }
    100% { box-shadow:0 0 0 0   rgba(66,133,244,0);   }
}
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="overlay" onclick="enableMap()"><span>탭하여 지도 조작</span></div>
</div>
<script>
var REF_LAT  = __REF_LAT__;
var REF_LON  = __REF_LON__;
var REF_NAME = __REF_NAME__;
var STATIONS = __STATIONS__;

var map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 19
}).addTo(map);

var bounds = [];

var refIcon = L.divIcon({ className:'', html:'<div class="ref-mk">★</div>', iconSize:[36,36] });
L.marker([REF_LAT, REF_LON], {icon: refIcon, zIndexOffset: 1000})
  .addTo(map)
  .bindPopup('<b style="color:__PRIMARY__">★ 기준 국소</b><br>' + REF_NAME)
  .openPopup();
bounds.push([REF_LAT, REF_LON]);

STATIONS.forEach(function(s) {
    var icon = L.divIcon({
        className: '',
        html: '<div class="num-mk">' + s.rank + '</div>',
        iconSize: [30, 30]
    });
    L.marker([s.lat, s.lon], {icon: icon})
      .addTo(map)
      .bindPopup(
        '<div class="popup-content">'
        + '<span class="popup-rank">' + s.rank + '위</span> ' + s.name + '<br>'
        + '거리: <b>' + s.dist + ' km</b><br>'
        + 'VENDOR: ' + s.vendor + '<br>'
        + 'PROD: ' + s.prod + '<br>'
        + 'W1: ' + s.wl + ' nm'
        + '</div>'
      );
    bounds.push([s.lat, s.lon]);
});

map.fitBounds(bounds, {padding: [40, 40]});

// 내 위치 버튼
var myLocMarker = null;
var myLocIcon = L.divIcon({className:'', html:'<div class="my-loc-mk"></div>', iconSize:[16,16]});

var LocControl = L.Control.extend({
    options: {position: 'topright'},
    onAdd: function() {
        var btn = L.DomUtil.create('button', 'loc-btn');
        btn.innerHTML = '📍 내 위치';
        L.DomEvent.disableClickPropagation(btn);
        btn.addEventListener('click', function() {
            if (!navigator.geolocation) { alert('위치 서비스 미지원'); return; }
            btn.disabled = true;
            btn.innerHTML = '위치 확인 중…';
            navigator.geolocation.getCurrentPosition(
                function(pos) {
                    var lat = pos.coords.latitude, lon = pos.coords.longitude;
                    map.flyTo([lat, lon], 14);
                    if (myLocMarker) map.removeLayer(myLocMarker);
                    myLocMarker = L.marker([lat, lon], {icon: myLocIcon, zIndexOffset:500})
                        .addTo(map).bindPopup('<b>📍 내 현재 위치</b>').openPopup();
                    btn.disabled = false;
                    btn.innerHTML = '📍 내 위치';
                },
                function(err) {
                    alert('위치를 가져올 수 없습니다.\n' + err.message);
                    btn.disabled = false;
                    btn.innerHTML = '📍 내 위치';
                },
                {enableHighAccuracy: true, timeout: 10000}
            );
        });
        return btn;
    }
});
new LocControl().addTo(map);

if (L.Browser.mobile) {
    map.dragging.disable();
    map.touchZoom.disable();
    if (map.tap) map.tap.disable();
} else {
    document.getElementById('overlay').style.display = 'none';
}

function enableMap() {
    document.getElementById('overlay').style.display = 'none';
    map.dragging.enable();
    map.touchZoom.enable();
    if (map.tap) map.tap.enable();
}
</script>
</body>
</html>"""

    return (html
            .replace("__REF_LAT__",  str(ref_lat))
            .replace("__REF_LON__",  str(ref_lon))
            .replace("__REF_NAME__", json.dumps(ref_name, ensure_ascii=False))
            .replace("__STATIONS__", stations_json)
            .replace("__ORANGE__",   ORANGE)
            .replace("__PRIMARY__",  PRIMARY))


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"<h2 style='color:{PRIMARY};font-size:1.1rem;margin-bottom:0.5rem'>📡 설정</h2>",
                unsafe_allow_html=True)

    if os.path.exists(SHARED_FILE):
        mtime = get_mtime(SHARED_FILE)
        mtime_str = pd.Timestamp(mtime, unit="s").strftime("%m-%d %H:%M")
        st.success(f"📊 공유 데이터\n\n업데이트: {mtime_str}")
        df = load_sfp_data(SHARED_FILE, mtime)
    elif os.path.exists(DEFAULT_FILE):
        df = load_sfp_data(DEFAULT_FILE, get_mtime(DEFAULT_FILE))
        st.caption("기본 파일 사용 중")
    else:
        df = None

    with st.expander("📂 파일 업데이트 (관리자)"):
        uploaded = st.file_uploader("SFP 정보 파일 (.xlsx)", type=["xlsx"])
        if uploaded:
            raw = uploaded.getvalue()
            with open(SHARED_FILE, "wb") as f:
                f.write(raw)
            st.success("✅ 업로드 완료 — 모든 기기에서 즉시 적용됩니다.")
            st.rerun()

    if df is None:
        st.warning("⚠️ 데이터 없음 — 파일을 업로드해주세요.")
        st.stop()


# ── Main ─────────────────────────────────────────────────────────────────────
st.markdown("<h1 class='app-title'>📡 AAU 동일 SFP 탐색기</h1>", unsafe_allow_html=True)

all_stations = sorted(df["station_name"].dropna().unique().tolist())

col_kw, col_sel = st.columns([1, 2])
with col_kw:
    keyword = st.text_input("🔍 국소명 검색", placeholder="예: 해인사")
with col_sel:
    filtered = [s for s in all_stations if keyword.lower() in s.lower()] if keyword else all_stations
    if not filtered:
        st.warning("검색 결과 없음")
        st.stop()
    ref_station = st.selectbox("기준 국소", filtered)

st.divider()

combos = sfp_combos_for_station(df, ref_station)
if combos.empty:
    st.warning("해당 국소의 SFP 정보가 없습니다.")
    st.stop()

combo_labels = [f"{r.vendor} / {r.vendorprod} / {r.wl}nm" for _, r in combos.iterrows()]
sel_idx = st.radio("SFP 조합 선택", range(len(combo_labels)),
                   format_func=lambda i: combo_labels[i], horizontal=True)
sel = combos.iloc[sel_idx]

n_results = st.radio("표시 국소 수", [10, 20, 30], horizontal=True)

st.divider()

# 기준 국소 좌표 추출
ref_row    = df[df["station_name"] == ref_station].iloc[0]
ref_lat    = ref_row["lat"]
ref_lon    = ref_row["lon"]
ref_lat_r  = ref_row["_lat_r"]
ref_lon_r  = ref_row["_lon_r"]

st.caption(f"★ 기준: **{ref_station}** · VENDOR: {sel.vendor} · PROD: {sel.vendorprod} · W1: {sel.wl} nm")

results = nearest_same_sfp(
    df, sel.vendor, sel.vendorprod, float(sel.wl),
    ref_lat, ref_lon, ref_lat_r, ref_lon_r, n_results
)

if results.empty:
    st.info("동일 VENDOR·VENDORPROD·W1 조합의 다른 국소가 없습니다.")
    st.stop()

st.markdown("#### 🗺️ 동일 SFP 국소 지도")
html = build_map_html(ref_lat, ref_lon, ref_station, results,
                      sel.vendor, sel.vendorprod, float(sel.wl))
components.html(html, height=500, scrolling=False)

# ── 탐색 결과 ─────────────────────────────────────────────────────────────────
st.markdown("#### 📋 탐색 결과")
st.caption("국소명을 탭하면 SFP 정보가 표시됩니다.")

result_html, result_height = build_result_html(results, sel.vendor, sel.vendorprod, float(sel.wl))
components.html(result_html, height=result_height, scrolling=False)

result_csv = results[["station_name", "distance_km"]].copy()
result_csv.index = range(1, len(result_csv) + 1)
result_csv.index.name = "순위"
result_csv.columns = ["국소명", "거리(km)"]
result_csv["VENDOR"]     = sel.vendor
result_csv["VENDORPROD"] = sel.vendorprod
result_csv["W1(nm)"]     = sel.wl
result_csv["거리(km)"]   = result_csv["거리(km)"].map("{:.2f}".format)
csv = result_csv.to_csv(encoding="utf-8-sig")
st.download_button("📥 결과 CSV 다운로드", csv, "sfp_match_result.csv", "text/csv")

st.caption(f"전체 데이터: {len(df):,}행 · 동일 SFP 매칭 국소: {len(results):,}개")
