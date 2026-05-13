import base64
import io
import os
import requests
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from math import radians, sin, cos, asin, sqrt
from streamlit_js_eval import get_geolocation

st.set_page_config(
    page_title="AAU 동일 SFP 탐색기",
    page_icon="⭐",
    layout="wide",
    initial_sidebar_state="expanded",
)

PRIMARY = "#2DB400"
ORANGE  = "#FF6600"
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(BASE_DIR, "SFP 정보.xlsx")
SHARED_FILE  = os.path.join(BASE_DIR, "_sfp_uploaded.xlsx")
GITHUB_REPO  = "ons-jinju/sfp-finder"
GITHUB_PATH  = "_sfp_uploaded.xlsx"

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
def _commit_to_github(file_bytes: bytes) -> bool:
    token = st.secrets.get("GITHUB_TOKEN", "")
    if not token:
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    existing = requests.get(url, headers=headers)
    sha = existing.json().get("sha") if existing.status_code == 200 else None
    payload = {"message": "Update SFP data file", "content": base64.b64encode(file_bytes).decode()}
    if sha:
        payload["sha"] = sha
    resp = requests.put(url, json=payload, headers=headers)
    return resp.status_code in (200, 201)


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


def nearest_by_wl(df, wl, center_lat, center_lon, n):
    matched = df[df["wl"] == wl].copy()
    site_df = (
        matched.groupby(["_lat_r", "_lon_r"], sort=False)
        .agg(
            station_name=("station_name", "first"),
            lat=("lat", "first"), lon=("lon", "first"),
            vendor=("vendor", "first"),
            vendorprod=("vendorprod", "first"),
            wl=("wl", "first"),
        )
        .reset_index()
    )
    site_df["distance_km"] = site_df.apply(
        lambda r: haversine_km(center_lat, center_lon, r["lat"], r["lon"]), axis=1
    )
    return site_df.nsmallest(n, "distance_km").reset_index(drop=True)


def render_pydeck_map(results, gps_lat=None, gps_lon=None, ref_lat=None, ref_lon=None, ref_name=None):
    layers_data = []

    if ref_lat is not None and ref_name:
        layers_data.append({
            "lat": ref_lat, "lon": ref_lon,
            "name": f"★ {ref_name}", "info": "기준 국소",
            "rank": "★", "color": [45, 180, 0, 240],
        })

    for i, row in results.iterrows():
        layers_data.append({
            "lat": row.lat, "lon": row.lon,
            "name": row.station_name,
            "info": f"{i + 1}위 · {row.distance_km:.2f} km",
            "rank": str(i + 1),
            "color": [255, 102, 0, 240],
        })

    if gps_lat and gps_lon:
        layers_data.append({
            "lat": gps_lat, "lon": gps_lon,
            "name": "📍 내 현재 위치", "info": "",
            "rank": "●", "color": [66, 133, 244, 240],
        })

    all_df = pd.DataFrame(layers_data)

    scatter_layer = pdk.Layer(
        "ScatterplotLayer",
        data=all_df,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_line_color=[255, 255, 255, 200],
        get_radius=1,
        radius_min_pixels=9,
        radius_max_pixels=16,
        stroked=True,
        line_width_min_pixels=2,
        pickable=True,
        auto_highlight=True,
    )

    text_layer = pdk.Layer(
        "TextLayer",
        data=all_df,
        get_position=["lon", "lat"],
        get_text="rank",
        get_size=13,
        get_color=[255, 255, 255, 255],
        get_alignment_baseline="'center'",
        get_anchor="'middle'",
        font_weight="bold",
        font_settings={"sdf": True, "fontSize": 64, "buffer": 8},
        billboard=True,
    )

    if gps_lat and gps_lon:
        center_lat, center_lon, zoom = gps_lat, gps_lon, 13
    elif ref_lat is not None and not results.empty:
        all_lats = [ref_lat] + results["lat"].tolist()
        all_lons = [ref_lon] + results["lon"].tolist()
        center_lat = (max(all_lats) + min(all_lats)) / 2
        center_lon = (max(all_lons) + min(all_lons)) / 2
        span = max(max(all_lats) - min(all_lats), max(all_lons) - min(all_lons))
        zoom = 13 if span < 0.05 else 11 if span < 0.2 else 10 if span < 0.5 else 9 if span < 1.5 else 8
    elif ref_lat is not None:
        center_lat, center_lon, zoom = ref_lat, ref_lon, 13
    else:
        center_lat, center_lon, zoom = 36.5, 127.5, 7

    view = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=zoom, pitch=0)

    tooltip = {
        "html": "<div style='font-size:13px;padding:4px 8px'><b>{name}</b><br/>{info}</div>",
        "style": {"backgroundColor": "rgba(0,0,0,0.75)", "color": "white", "borderRadius": "6px"},
    }

    return pdk.Deck(
        map_style="road",
        initial_view_state=view,
        layers=[scatter_layer, text_layer],
        tooltip=tooltip,
    )


def build_result_html(results, vendor=None, prod=None, wl=None, per_row_sfp=False):
    rows = ""
    for i, row in results.iterrows():
        rank = i + 1
        sfp_info = (
            f"{row.vendor} / {row.vendorprod} / {row.wl} nm"
            if per_row_sfp
            else f"{vendor} / {prod} / {wl} nm"
        )
        rows += (
            "<tr class='mr' onclick='toggle(__R__)'>".replace("__R__", str(rank)) +
            "<td class='rk'>" + str(rank) + "</td>" +
            "<td class='nm'>" + str(row.station_name) + "</td>" +
            "<td class='ds'>" + f"{row.distance_km:.2f}" + " km</td></tr>" +
            "<tr id='d__R__' class='dr'>".replace("__R__", str(rank)) +
            "<td></td><td colspan='2' class='dc'>" + sfp_info +
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
            saved = _commit_to_github(raw)
            msg = "✅ 업로드 완료 — 영구 저장됩니다." if saved else "✅ 업로드 완료 — 앱 재시작 시 재업로드 필요."
            st.success(msg)
            st.rerun()

    if df is None:
        st.warning("⚠️ 데이터 없음 — 파일을 업로드해주세요.")
        st.stop()


# ── Main ─────────────────────────────────────────────────────────────────────
st.markdown("<h1 class='app-title'>📡 AAU 동일 SFP 탐색기</h1>", unsafe_allow_html=True)

if "gps_lat" not in st.session_state:
    st.session_state.gps_lat = None
    st.session_state.gps_lon = None
    st.session_state.gps_active = False

# 탭 앞에서 항상 렌더링해야 컴포넌트 트리가 안정적으로 유지되어 탭 위치가 보존됨
_loc = get_geolocation()

gps_lat = st.session_state.gps_lat
gps_lon = st.session_state.gps_lon

if st.session_state.gps_active:
    if _loc and _loc.get("coords"):
        st.session_state.gps_lat = _loc["coords"]["latitude"]
        st.session_state.gps_lon = _loc["coords"]["longitude"]
        st.session_state.gps_active = False
        gps_lat = st.session_state.gps_lat
        gps_lon = st.session_state.gps_lon

tab_station, tab_gps = st.tabs(["🏙️ 국소 기준 탐색", "📍 내 위치 기준 탐색"])

# ── Tab 1: 국소 기준 ──────────────────────────────────────────────────────────
with tab_station:
    all_stations = sorted(df["station_name"].dropna().unique().tolist())

    col_kw, col_sel, col_gps_btn = st.columns([1, 2, 1])
    with col_kw:
        keyword = st.text_input("🔍 국소명 검색", placeholder="예: 해인사", key="kw_station")
    with col_sel:
        filtered = [s for s in all_stations if keyword.lower() in s.lower()] if keyword else all_stations
        if filtered:
            ref_station = st.selectbox("기준 국소", filtered, key="sel_station")
        else:
            st.warning("검색 결과 없음")
            ref_station = None
    with col_gps_btn:
        st.write("　")
        if st.button("📍 현재위치로 이동", use_container_width=True, key="gps_btn_station"):
            st.session_state.gps_active = True

    if ref_station:
        st.divider()
        combos = sfp_combos_for_station(df, ref_station)
        if combos.empty:
            st.warning("해당 국소의 SFP 정보가 없습니다.")
        else:
            combo_labels = [f"{r.vendor} / {r.vendorprod} / {r.wl}nm" for _, r in combos.iterrows()]
            sel_idx = st.radio("SFP 조합 선택", range(len(combo_labels)),
                               format_func=lambda i: combo_labels[i], horizontal=True, key="sfp_combo")
            sel = combos.iloc[sel_idx]

            n_results = st.radio("표시 국소 수", [10, 20, 30], horizontal=True, key="n_station")

            st.divider()

            ref_row   = df[df["station_name"] == ref_station].iloc[0]
            ref_lat   = ref_row["lat"]
            ref_lon   = ref_row["lon"]
            ref_lat_r = ref_row["_lat_r"]
            ref_lon_r = ref_row["_lon_r"]

            st.caption(f"★ 기준: **{ref_station}** · VENDOR: {sel.vendor} · PROD: {sel.vendorprod} · W1: {sel.wl} nm")

            results = nearest_same_sfp(
                df, sel.vendor, sel.vendorprod, float(sel.wl),
                ref_lat, ref_lon, ref_lat_r, ref_lon_r, n_results
            )

            if results.empty:
                st.info("동일 VENDOR·VENDORPROD·W1 조합의 다른 국소가 없습니다.")
            else:
                st.markdown("#### 🗺️ 동일 SFP 국소 지도")
                st.pydeck_chart(
                    render_pydeck_map(results, gps_lat=gps_lat, gps_lon=gps_lon,
                                      ref_lat=ref_lat, ref_lon=ref_lon, ref_name=ref_station),
                    use_container_width=True,
                )

                st.markdown("#### 📋 탐색 결과")
                st.caption("국소명을 탭하면 SFP 정보가 표시됩니다.")

                result_html, result_height = build_result_html(
                    results, sel.vendor, sel.vendorprod, float(sel.wl)
                )
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
                st.download_button("📥 결과 CSV 다운로드", csv, "sfp_match_result.csv",
                                   "text/csv", key="csv_station")

                st.caption(f"전체 데이터: {len(df):,}행 · 동일 SFP 매칭 국소: {len(results):,}개")


# ── Tab 2: 내 위치 기준 ───────────────────────────────────────────────────────
with tab_gps:
    col_gps_info, col_wl_sel = st.columns([1, 2])

    with col_gps_info:
        if st.button("📍 현재 위치 가져오기", use_container_width=True, key="gps_btn_gps"):
            st.session_state.gps_active = True
        if gps_lat and gps_lon:
            st.success(f"📍 위치 확인\n\n{gps_lat:.5f}, {gps_lon:.5f}")
        elif st.session_state.gps_active:
            st.caption("📍 GPS 권한을 허용해주세요...")
        else:
            st.info("버튼을 눌러\n현재 위치를 가져오세요.")

    with col_wl_sel:
        wl_site_counts = (
            df.dropna(subset=["wl"])
            .groupby(["wl", "_lat_r", "_lon_r"])
            .size()
            .reset_index(name="_n")
            .groupby("wl")
            .size()
            .sort_values(ascending=False)
        )
        all_wl = wl_site_counts.index.tolist()
        wl_labels = [f"{w} nm  ({wl_site_counts[w]}개 국소)" for w in all_wl]
        wl_idx = st.selectbox(
            "W1 (파장) 선택 — 국소 많은 순",
            range(len(all_wl)),
            format_func=lambda i: wl_labels[i],
            key="wl_select",
        )
        selected_wl = all_wl[wl_idx]
        n_results_gps = st.radio("표시 국소 수", [10, 20, 30], horizontal=True, key="n_gps")

    st.divider()

    if not (gps_lat and gps_lon):
        st.warning("📍 현재 위치를 먼저 가져와주세요.")
    else:
        results_gps = nearest_by_wl(df, selected_wl, gps_lat, gps_lon, n_results_gps)

        if results_gps.empty:
            st.info(f"W1 {selected_wl} nm에 해당하는 국소가 없습니다.")
        else:
            st.caption(f"📍 현재 위치 기준 · W1: {selected_wl} nm · 인근 {len(results_gps)}개 국소")

            st.markdown("#### 🗺️ 인근 국소 지도")
            st.pydeck_chart(
                render_pydeck_map(results_gps, gps_lat=gps_lat, gps_lon=gps_lon),
                use_container_width=True,
            )

            st.markdown("#### 📋 탐색 결과")
            st.caption("국소명을 탭하면 SFP 정보가 표시됩니다.")

            result_html_gps, result_height_gps = build_result_html(results_gps, per_row_sfp=True)
            components.html(result_html_gps, height=result_height_gps, scrolling=False)

            result_csv_gps = results_gps[["station_name", "distance_km", "vendor", "vendorprod", "wl"]].copy()
            result_csv_gps.index = range(1, len(result_csv_gps) + 1)
            result_csv_gps.index.name = "순위"
            result_csv_gps.columns = ["국소명", "거리(km)", "VENDOR", "VENDORPROD", "W1(nm)"]
            result_csv_gps["거리(km)"] = result_csv_gps["거리(km)"].map("{:.2f}".format)
            csv_gps = result_csv_gps.to_csv(encoding="utf-8-sig")
            st.download_button("📥 결과 CSV 다운로드", csv_gps, "sfp_gps_result.csv",
                               "text/csv", key="csv_gps")

            st.caption(f"전체 데이터: {len(df):,}행 · W1 {selected_wl} nm 매칭 국소: {len(results_gps):,}개")
