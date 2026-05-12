import io
import json
import os
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from math import radians, sin, cos, asin, sqrt

st.set_page_config(
    page_title="5G SFP 국소 탐색기",
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
    .block-container { padding-left: 1rem !important; padding-right: 1rem !important; }
}
.sfp-info-box {
    background: #f6fbf3;
    border-left: 5px solid #2DB400;
    padding: 0.75rem 1.25rem;
    border-radius: 6px;
    margin-bottom: 1rem;
    font-size: 0.95rem;
    line-height: 1.8;
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


def nearest_same_sfp(df, vendor, vendorprod, wl, user_lat, user_lon, n):
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
    site_df["distance_km"] = site_df.apply(
        lambda r: haversine_km(user_lat, user_lon, r["lat"], r["lon"]), axis=1
    )
    return site_df.nsmallest(n, "distance_km").reset_index(drop=True)


# ── Leaflet.js 지도 HTML 생성 (API 키 불필요, 어디서든 동작) ──────────────────
def build_map_html(user_lat, user_lon, results, vendor, prod, wl):
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
#map { width:100%; height:490px; }
.num-mk {
    width:30px; height:30px; background:__ORANGE__;
    border-radius:50%; color:#fff; font-weight:bold;
    font-size:13px; display:flex; align-items:center;
    justify-content:center; cursor:pointer;
    box-shadow:0 2px 6px rgba(0,0,0,.4); border:2px solid #fff;
    margin-left:-15px; margin-top:-15px;
}
.user-mk {
    width:20px; height:20px; background:__PRIMARY__;
    border-radius:50%; border:3px solid #fff;
    box-shadow:0 2px 6px rgba(0,0,0,.4);
    margin-left:-10px; margin-top:-10px;
}
.popup-content { font-size:13px; line-height:1.8;
    font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; min-width:180px; }
.popup-rank { color:__ORANGE__; font-weight:bold; font-size:15px; }
</style>
</head>
<body>
<div id="map"></div>
<script>
var USER_LAT = __LAT__;
var USER_LON = __LON__;
var STATIONS = __STATIONS__;

var map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap', maxZoom: 19
}).addTo(map);

var bounds = [];

// 내 위치 (초록 마커)
var userIcon = L.divIcon({ className:'', html:'<div class="user-mk"></div>', iconSize:[20,20] });
L.marker([USER_LAT, USER_LON], {icon: userIcon})
  .addTo(map)
  .bindPopup('<b style="color:__PRIMARY__">📍 내 위치</b>');
bounds.push([USER_LAT, USER_LON]);

// 국소 마커 (번호)
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

map.fitBounds(bounds, {padding: [30, 30]});
</script>
</body>
</html>"""

    return (html
            .replace("__LAT__",      str(user_lat))
            .replace("__LON__",      str(user_lon))
            .replace("__STATIONS__", stations_json)
            .replace("__ORANGE__",   ORANGE)
            .replace("__PRIMARY__",  PRIMARY))


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"<h2 style='color:{PRIMARY}'>📡 5G SFP 탐색</h2>", unsafe_allow_html=True)
    st.markdown("---")

    st.markdown("### 📂 데이터")
    if os.path.exists(SHARED_FILE):
        mtime = get_mtime(SHARED_FILE)
        mtime_str = pd.Timestamp(mtime, unit="s").strftime("%m-%d %H:%M")
        st.success(f"📊 공유 데이터 적용 중\n\n업데이트: {mtime_str}")
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
        st.warning("⚠️ 데이터 없음 — 위 메뉴에서 파일을 업로드해주세요.")
        st.stop()

    all_stations = sorted(df["station_name"].dropna().unique().tolist())

    st.markdown("---")
    st.markdown("### 📍 내 위치")
    st.caption("카카오·네이버 지도에서 좌표 확인 후 입력")
    user_lat = st.number_input("위도", value=35.1800, min_value=33.0, max_value=39.0,
                               step=0.0001, format="%.4f")
    user_lon = st.number_input("경도", value=128.1000, min_value=124.0, max_value=132.0,
                               step=0.0001, format="%.4f")

    st.markdown("---")
    st.markdown("### 🎯 기준 국소 선택")
    keyword = st.text_input("국소명 검색", placeholder="예: 진주칠암")
    filtered = [s for s in all_stations if keyword.lower() in s.lower()] if keyword else all_stations

    if not filtered:
        st.warning("검색 결과 없음")
        st.stop()

    ref_station = st.selectbox("국소 선택", filtered, label_visibility="collapsed")

    combos = sfp_combos_for_station(df, ref_station)
    if combos.empty:
        st.warning("해당 국소의 SFP 정보가 없습니다.")
        st.stop()

    combo_labels = [f"{r.vendor} / {r.vendorprod} / {r.wl}nm" for _, r in combos.iterrows()]
    sel_idx = st.radio("SFP 조합", range(len(combo_labels)),
                       format_func=lambda i: combo_labels[i])
    sel = combos.iloc[sel_idx]
    n_results = st.slider("표시 국소 수", 5, 20, 10)


# ── Main ─────────────────────────────────────────────────────────────────────
st.markdown(f"<h1 style='color:{PRIMARY}'>📡 5G SFP 국소 탐색기</h1>", unsafe_allow_html=True)

st.markdown(
    f"""<div class='sfp-info-box'>
    <b>🎯 기준 국소</b>: {ref_station}<br>
    <b>VENDOR</b>: {sel.vendor}&nbsp;&nbsp;
    <b>VENDORPROD</b>: {sel.vendorprod}&nbsp;&nbsp;
    <b>W1</b>: {sel.wl} nm
    </div>""",
    unsafe_allow_html=True,
)

results = nearest_same_sfp(df, sel.vendor, sel.vendorprod, float(sel.wl), user_lat, user_lon, n_results)

if results.empty:
    st.info("동일 VENDOR·VENDORPROD·W1 조합의 국소가 없습니다.")
    st.stop()

# ── 지도 (카카오맵 우선, 없으면 OpenStreetMap) ────────────────────────────────
st.markdown("#### 🗺️ 동일 SFP 국소 지도")

html = build_map_html(user_lat, user_lon, results,
                      sel.vendor, sel.vendorprod, float(sel.wl))
components.html(html, height=500, scrolling=False)

# ── 결과 테이블 ───────────────────────────────────────────────────────────────
st.markdown("#### 📋 탐색 결과")

col_rank, col_table = st.columns([1, 4])

with col_rank:
    summary = results[["station_name", "distance_km"]].copy()
    summary.index = range(1, len(summary) + 1)
    summary.columns = ["국소명", "거리(km)"]
    summary["거리(km)"] = summary["거리(km)"].map("{:.2f}".format)
    st.dataframe(summary, use_container_width=True, height=400)

with col_table:
    detail_keys = set(zip(results["_lat_r"], results["_lon_r"]))
    detail_df = df[
        df.apply(lambda r: (r["_lat_r"], r["_lon_r"]) in detail_keys, axis=1) &
        (df["vendor"] == sel.vendor) &
        (df["vendorprod"] == sel.vendorprod) &
        (df["wl"] == float(sel.wl))
    ].copy()
    detail_df["distance_km"] = detail_df.apply(
        lambda r: haversine_km(user_lat, user_lon, r["lat"], r["lon"]), axis=1
    )
    coord_to_rank = {(row["_lat_r"], row["_lon_r"]): i + 1 for i, row in results.iterrows()}
    detail_df["순위"] = detail_df.apply(lambda r: coord_to_rank.get((r["_lat_r"], r["_lon_r"])), axis=1)

    result_df = detail_df.rename(columns={
        "station_name": "국소명", "vendor": "VENDOR",
        "vendorprod": "VENDORPROD", "wl": "W1 (nm)", "distance_km": "거리 (km)",
    })[["순위", "국소명", "거리 (km)", "VENDOR", "VENDORPROD", "W1 (nm)"]].drop_duplicates()
    result_df = result_df.sort_values("순위")
    result_df["거리 (km)"] = result_df["거리 (km)"].map("{:.2f}".format)
    st.dataframe(result_df, use_container_width=True, height=400)

csv = result_df.to_csv(index=False, encoding="utf-8-sig")
st.download_button("📥 결과 CSV 다운로드", csv, "sfp_match_result.csv", "text/csv")

st.caption(f"전체 데이터: {len(df):,}행 · 동일 SFP 매칭 국소: {len(results):,}개")
