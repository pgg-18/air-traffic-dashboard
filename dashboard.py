import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
from pipeline import check_and_update

st.set_page_config(layout="wide")

if 'updated' not in st.session_state:
    check_and_update()
    st.session_state['updated'] = True

st.markdown("""
    <style>
    .stApp {
        background-color: #0f0f0f;
        color: white;
    }
    header[data-testid="stHeader"] {
    background: #0f0f0f !important;
    }
    [data-testid="stMetricValue"] p {
        color: white !important;
    }
    [data-testid="stMetricLabel"] p {
        color: white !important;
    }
    [data-testid="stMetricValue"] {
        color: white !important;
    }
    p {
        color: white !important;
    }
    </style>
""", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["General Overview", "Airport Wise Data"])

@st.cache_data
def load_data():
    df = pd.read_excel("AirTraffic_Full2.xlsx", sheet_name="Sheet1")
    df = df.drop(columns=['Unnamed: 6', 'Unnamed: 7'], errors='ignore')
    df = df.dropna(subset=['Financial Year', 'Metric', 'Type'])
    df = df.reset_index(drop=True)
    df.index = df.index + 1
    df['YoY%'] = ((df['FY Value'] - df['Prev FY Value']) / df['Prev FY Value']) * 100

    excel_years = df['Financial Year'].unique().tolist()

    try:
        conn = sqlite3.connect("airtraffic.db")
        df_sql = pd.read_sql("SELECT * FROM monthly_traffic", conn)
        conn.close()

        if not df_sql.empty:
            df_latest = df_sql[df_sql['Type'].isin(['International', 'Domestic', 'Total'])]
            df_latest = df_latest.groupby(['Year', 'Metric', 'Type']).agg({'Month_Value': 'sum', 'Prev_Month_Value': 'sum'}).reset_index()
            df_latest = df_latest.rename(columns={
                'Year': 'Financial Year',
                'Month_Value': 'FY Value',
                'Prev_Month_Value': 'Prev FY Value'
            })
            df_latest['YoY%'] = ((df_latest['FY Value'] - df_latest['Prev FY Value']) / df_latest['Prev FY Value']) * 100
            df_latest = df_latest[['Financial Year', 'Metric', 'Type', 'FY Value', 'Prev FY Value', 'YoY%']]
            df_latest = df_latest[~df_latest['Financial Year'].isin(excel_years)]
            df = pd.concat([df, df_latest], ignore_index=True)
    except:
        pass

    df_total = df.groupby(['Financial Year', 'Metric'])['FY Value'].sum().reset_index()
    df_total = df_total.sort_values(['Metric', 'Financial Year'])
    df_total['YoY%'] = df_total.groupby('Metric')['FY Value'].pct_change() * 100
    return df, df_total, excel_years

def _add_split(df):
    """Add International / Domestic share columns (0 where the airport has no traffic)."""
    total = df['FY Total'].replace(0, pd.NA)
    df['Intl %'] = (df['FY Intl'] / total * 100).fillna(0).round(1)
    df['Dom %'] = (df['FY Domestic'] / total * 100).fillna(0).round(1)
    return df

@st.cache_data
def load_annex3(fy_short, cutoff=0):
    if fy_short == '26-27':
        conn = sqlite3.connect("airtraffic.db")
        df_intl = pd.read_sql("SELECT Airport, Cumulative_Value as 'FY Intl', Prev_Cumulative_Value as 'Prev Intl' FROM airport_traffic WHERE Year='2026-2027' AND Pass_Type='International'", conn)
        df_dom = pd.read_sql("SELECT Airport, Cumulative_Value as 'FY Domestic', Prev_Cumulative_Value as 'Prev Domestic' FROM airport_traffic WHERE Year='2026-2027' AND Pass_Type='Domestic'", conn)
        df_total = pd.read_sql("SELECT Airport, Airport_Category, Cumulative_Value as 'FY Total', Prev_Cumulative_Value as 'Prev Total' FROM airport_traffic WHERE Year='2026-2027' AND Pass_Type='Total'", conn)
        conn.close()
        df = df_total.merge(df_intl, on='Airport', how='left').merge(df_dom, on='Airport', how='left')
        df['FY Intl'] = df['FY Intl'].fillna(0)
        df['FY Domestic'] = df['FY Domestic'].fillna(0)
        df['Change%'] = ((df['FY Total'] - df['Prev Total']) / df['Prev Total']) * 100
        df = df.rename(columns={'Airport_Category': 'Airport Category'})
        df = _add_split(df)
        df = df[['Airport', 'FY Total', 'FY Intl', 'FY Domestic', 'Intl %', 'Dom %', 'Prev Total', 'Change%', 'Airport Category']]
        df = df[df['FY Total'] >= cutoff].sort_values('FY Total', ascending=False).reset_index(drop=True)
        return df

    fy_full = "20" + fy_short[:2] + "-20" + fy_short[3:]
    try:
        conn = sqlite3.connect("airtraffic.db")
        df = pd.read_sql(
            "SELECT Airport, FY_Total as 'FY Total', FY_Intl as 'FY Intl', "
            "FY_Domestic as 'FY Domestic', Prev_Total as 'Prev Total', "
            "Airport_Category as 'Airport Category' FROM airport_yearly WHERE FY = ?",
            conn, params=(fy_full,))
        conn.close()
        if df.empty:
            raise ValueError("no airport_yearly rows for " + fy_full)
        df['Change%'] = ((df['FY Total'] - df['Prev Total']) / df['Prev Total']) * 100
        df = _add_split(df)
        df = df[['Airport', 'FY Total', 'FY Intl', 'FY Domestic', 'Intl %', 'Dom %', 'Prev Total', 'Change%', 'Airport Category']]
        df = df[df['FY Total'] >= cutoff].sort_values('FY Total', ascending=False).reset_index(drop=True)
        df.index = df.index + 1
        return df
    except Exception:
        df = pd.read_excel("AirTraffic_Full2.xlsx", sheet_name=f"FY{fy_short}_Annex3", header=1)
        df.columns = ['S.No', 'Airport', 'FY Total', 'FY Intl', 'FY Domestic', 'Prev Total', 'Change%', 'Airport Category']
        df = df[df['S.No'].apply(lambda x: str(x).isdigit())]
        df = df.reset_index(drop=True)
        for c in ['FY Total', 'FY Intl', 'FY Domestic', 'Prev Total']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df['Change%'] = ((df['FY Total'] - df['Prev Total']) / df['Prev Total']) * 100
        df = df.dropna(subset=['Airport', 'FY Total'])
        df = _add_split(df)
        df = df[df['FY Total'] >= cutoff].reset_index(drop=True)
        df.index = df.index + 1
        return df

def calculate_extra_metrics(metric, year, total_val, df_tot):
    """Calculates CAGR, Averages, and fetches busiest/relaxed months from the DB."""
    busiest, relaxed = "N/A", "N/A"
    num_months = 12
    try:
        conn = sqlite3.connect("airtraffic.db")
        df_m = pd.read_sql(f"SELECT Month, SUM(Month_Value) as val FROM monthly_traffic WHERE Year='{year}' AND Metric='{metric}' AND Type='Total' GROUP BY Month", conn)
        conn.close()
        if not df_m.empty:
            busiest = df_m.loc[df_m['val'].idxmax()]['Month']
            relaxed = df_m.loc[df_m['val'].idxmin()]['Month']
            num_months = len(df_m)
    except:
        pass

    per_month = total_val / num_months if num_months > 0 else 0
    # Approximate daily based on available data months (30.41 days avg per month)
    per_day = total_val / (num_months * 30.41) if num_months > 0 else 0

    cagr_str = "N/A"
    try:
        df_m_tot = df_tot[df_tot['Metric'] == metric]
        if not df_m_tot.empty:
            min_year = df_m_tot['Financial Year'].min()
            num_years = int(year[:4]) - int(min_year[:4])
            if num_years > 0:
                base_val = df_m_tot[df_m_tot['Financial Year'] == min_year]['FY Value'].values[0]
                if base_val > 0:
                    cagr = ((total_val / base_val) ** (1 / num_years) - 1) * 100
                    cagr_str = f"{cagr:.1f}%"
    except:
        pass

    p_day_str = f"{int(per_day):,}" if per_day > 0 else "N/A"
    p_month_str = f"{int(per_month):,}" if per_month > 0 else "N/A"

    return cagr_str, p_day_str, p_month_str, busiest, relaxed

def normalize_airport_name(name):
    """Standardizes airport names to fix historical trend line breaks across years."""
    name = str(name).upper()
    for city in ['DELHI', 'MUMBAI', 'BENGALURU', 'BANGALORE', 'HYDERABAD', 'KOLKATA', 'CHENNAI', 'AHMEDABAD', 'GOA']:
        if city in name: 
            return 'BENGALURU' if city == 'BANGALORE' else city
    return name.strip()


df, df_total, excel_years = load_data()

with tab1:
    st.title("India Air Traffic Dashboard")
    st.subheader("Filter")
    years = df['Financial Year'].unique().tolist()
    selected_year = st.selectbox("Select Financial Year", years)
    df_filtered = df[df['Financial Year'] == selected_year]

    try:
        if selected_year not in excel_years:
            conn = sqlite3.connect("airtraffic.db")
            df_months = pd.read_sql(f"SELECT DISTINCT Month FROM monthly_traffic WHERE Year = '{selected_year}'", conn)
            conn.close()
            if not df_months.empty:
                months_available = df_months['Month'].tolist()
                st.warning(f"Partial year data — figures represent {months_available[0]} to {months_available[-1]} {selected_year} only, not a full financial year.")
    except:
        pass

    st.subheader("Monthly Comparison Across Years")

    try:
        conn = sqlite3.connect("airtraffic.db")
        all_available_months = pd.read_sql(
            "SELECT DISTINCT Month FROM monthly_traffic WHERE Type IS NOT NULL ORDER BY Month",
            conn
        )['Month'].tolist()
        
        if selected_year not in excel_years:
            year_months = pd.read_sql(
                f"SELECT DISTINCT Month FROM monthly_traffic WHERE Year = '{selected_year}' AND Type IS NOT NULL ORDER BY Month",
                conn
            )['Month'].tolist()
        else:
            year_months = all_available_months
        conn.close()
    except:
        all_available_months = []
        year_months = []

    col_mon1, col_mon2 = st.columns(2)
    with col_mon1:
        selected_months = st.multiselect(
            "Select Month(s) to Compare Across Years",
            options=year_months,
            default=[],
            placeholder="Leave empty for full year"
        )
    with col_mon2:
        selected_metric_m = st.selectbox("Select Metric", ['Passengers', 'Movements', 'Freight'])

    try:
        conn = sqlite3.connect("airtraffic.db")
        if selected_months:
            months_str = ', '.join([f"'{m}'" for m in selected_months])
            title = f"{', '.join(selected_months)} {selected_metric_m} Across Financial Years"
            query = f"""
                SELECT Year, SUM(Month_Value) as Value
                FROM monthly_traffic
                WHERE Month IN ({months_str})
                AND Metric = '{selected_metric_m}'
                AND Type = 'Total'
                GROUP BY Year
                ORDER BY Year
            """
        else:
            title = f"Full Year {selected_metric_m} Across Financial Years"
            query = f"""
                SELECT Year, SUM(Month_Value) as Value
                FROM monthly_traffic
                WHERE Metric = '{selected_metric_m}'
                AND Type = 'Total'
                GROUP BY Year
                ORDER BY Year
            """
        
        df_month_compare = pd.read_sql(query, conn)
        conn.close()

        if not df_month_compare.empty:
            fig_mc = px.bar(
                df_month_compare,
                x='Year',
                y='Value',
                title=title,
                color='Value',
                color_continuous_scale='blues'
            )
            fig_mc.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig_mc, use_container_width=True)
    except:
        pass

    # --- PASSENGERS METRICS ---
    st.subheader("Key Metrics - Passengers")
    latest_passengers = df_filtered[df_filtered['Metric'] == 'Passengers']
    total_pax = latest_passengers[latest_passengers['Type'] != 'Total']['FY Value'].sum()
    intl_pax = latest_passengers[latest_passengers['Type'] == 'International']['FY Value'].values[0] if not latest_passengers[latest_passengers['Type'] == 'International'].empty else 0
    dom_pax = latest_passengers[latest_passengers['Type'] == 'Domestic']['FY Value'].values[0] if not latest_passengers[latest_passengers['Type'] == 'Domestic'].empty else 0
    
    # NEW FIX FOR TOTAL YoY% (Passengers)
    prev_total_pax = latest_passengers[latest_passengers['Type'] != 'Total']['Prev FY Value'].sum()
    total_yoy = ((total_pax - prev_total_pax) / prev_total_pax * 100) if prev_total_pax > 0 else 0
    
    intl_yoy = latest_passengers[latest_passengers['Type'] == 'International']['YoY%'].values[0] if not latest_passengers[latest_passengers['Type'] == 'International'].empty else 0
    dom_yoy = latest_passengers[latest_passengers['Type'] == 'Domestic']['YoY%'].values[0] if not latest_passengers[latest_passengers['Type'] == 'Domestic'].empty else 0

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1: st.metric("Total Passengers", f"{total_pax/1e6:.1f}M", f"{total_yoy:.1f}%")
    with col_m2: st.metric("International Passengers", f"{intl_pax/1e6:.1f}M", f"{intl_yoy:.1f}%")
    with col_m3: st.metric("Domestic Passengers", f"{dom_pax/1e6:.1f}M", f"{dom_yoy:.1f}%")
    
    # Advanced Passenger Metrics
    cagr_p, p_day_p, p_month_p, b_mon_p, r_mon_p = calculate_extra_metrics('Passengers', selected_year, total_pax, df_total)
    cp1, cp2, cp3, cp4, cp5 = st.columns(5)
    cp1.metric("CAGR (vs Base)", cagr_p)
    cp2.metric("Per Day Avg", p_day_p)
    cp3.metric("Per Month Avg", p_month_p)
    cp4.metric("Busiest Mth", b_mon_p)
    cp5.metric("Relaxed Mth", r_mon_p)
    st.markdown("<br>", unsafe_allow_html=True)


    # --- MOVEMENTS METRICS ---
    st.subheader("Key Metrics - Movements")
    latest_movements = df_filtered[df_filtered['Metric'] == 'Movements']
    total_mov = latest_movements[latest_movements['Type'] != 'Total']['FY Value'].sum()
    intl_mov = latest_movements[latest_movements['Type'] == 'International']['FY Value'].values[0] if not latest_movements[latest_movements['Type'] == 'International'].empty else 0
    dom_mov = latest_movements[latest_movements['Type'] == 'Domestic']['FY Value'].values[0] if not latest_movements[latest_movements['Type'] == 'Domestic'].empty else 0
    
    # NEW FIX FOR TOTAL YoY% (Movements)
    prev_total_mov = latest_movements[latest_movements['Type'] != 'Total']['Prev FY Value'].sum()
    total_mov_yoy = ((total_mov - prev_total_mov) / prev_total_mov * 100) if prev_total_mov > 0 else 0
    
    intl_mov_yoy = latest_movements[latest_movements['Type'] == 'International']['YoY%'].values[0] if not latest_movements[latest_movements['Type'] == 'International'].empty else 0
    dom_mov_yoy = latest_movements[latest_movements['Type'] == 'Domestic']['YoY%'].values[0] if not latest_movements[latest_movements['Type'] == 'Domestic'].empty else 0

    col_m4, col_m5, col_m6 = st.columns(3)
    with col_m4: st.metric("Total Movements", f"{total_mov/1e6:.2f}M", f"{total_mov_yoy:.1f}%")
    with col_m5: st.metric("International Movements", f"{intl_mov/1e6:.2f}M", f"{intl_mov_yoy:.1f}%")
    with col_m6: st.metric("Domestic Movements", f"{dom_mov/1e6:.2f}M", f"{dom_mov_yoy:.1f}%")

    # Advanced Movement Metrics
    cagr_m, p_day_m, p_month_m, b_mon_m, r_mon_m = calculate_extra_metrics('Movements', selected_year, total_mov, df_total)
    cm1, cm2, cm3, cm4, cm5 = st.columns(5)
    cm1.metric("CAGR (vs Base)", cagr_m)
    cm2.metric("Per Day Avg", p_day_m)
    cm3.metric("Per Month Avg", p_month_m)
    cm4.metric("Busiest Mth", b_mon_m)
    cm5.metric("Relaxed Mth", r_mon_m)
    st.markdown("<br>", unsafe_allow_html=True)


    st.subheader("Raw Data")
    st.dataframe(df_filtered)
    st.markdown("# National Trends")

    col1, col2 = st.columns(2)
    with col1:
        df_movements = df_total[df_total['Metric'] == 'Movements']
        fig2 = px.line(df_movements, x='Financial Year', y='FY Value', title='Total Aircraft Movements Year Wise', markers=True)
        fig2.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
        fig2.update_traces(marker_color="royalblue", line_color="white", line_width=5)
        st.plotly_chart(fig2, use_container_width=True)
    with col2:
        df_freight = df_total[df_total['Metric'] == 'Freight']
        fig3 = px.line(df_freight, x='Financial Year', y='FY Value', title='Total Freight Year Wise', markers=True)
        fig3.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
        fig3.update_traces(marker_color="royalblue", line_color="white", line_width=5)
        st.plotly_chart(fig3, use_container_width=True)

    df_passengers = df_total[df_total['Metric'] == 'Passengers']
    fig = px.line(df_passengers, x='Financial Year', y='FY Value', title='Total Passengers Year Wise', markers=True)
    fig.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
    fig.update_traces(marker_color="royalblue", line_color="white", line_width=5)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("International vs Domestic Split")
    df_split = df[df['Type'] != 'Total'][['Financial Year', 'Metric', 'Type', 'FY Value']]
    fig4 = px.bar(
        df_split,
        x='Financial Year',
        y='FY Value',
        color='Type',
        barmode='stack',
        facet_col='Metric',
        title='International vs Domestic Split by Metric',
        color_discrete_map={'International': 'red', 'Domestic': 'lightskyblue'}
    )
    fig4.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
    fig4.for_each_yaxis(lambda y: y.update(matches=None))
    fig4.for_each_annotation(lambda a: a.update(font=dict(color='white')))
    st.plotly_chart(fig4, use_container_width=True)

    st.subheader("Year on Year Growth %")
    fig5 = px.bar(
        df_total.dropna(subset=['YoY%']),
        x='Financial Year',
        y='YoY%',
        color='Metric',
        barmode='group',
        title='Year on Year Growth % by Metric',
        color_discrete_map={'Passengers': 'royalblue', 'Movements': 'lightcoral', 'Freight': 'lightgreen'}
    )
    fig5.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
    fig5.add_hline(y=0, line_color='white', line_dash='dash')
    st.plotly_chart(fig5, use_container_width=True)

with tab2:
    years = df['Financial Year'].unique().tolist()
    fy = st.selectbox("Choose Financial Year", years)
    fy_short = fy[2:4] + "-" + fy[7:]
    cutoff = st.slider(
        "Show airports handling at least this many passengers (full year)",
        min_value=0, max_value=1000000, value=0, step=10000,
        help="Drag right to hide the smallest airports. 0 shows every airport."
    )
    df_annex = load_annex3(fy_short, cutoff)
    
    if not df_annex.empty:
        _t = df_annex['FY Total'].sum()
        _i = df_annex['FY Intl'].sum()
        
        # New additions for Largest and Smallest Airport dynamically fetched
        largest_airport = df_annex.iloc[0]['Airport']
        largest_val = df_annex.iloc[0]['FY Total']
        smallest_airport = df_annex.iloc[-1]['Airport']
        smallest_val = df_annex.iloc[-1]['FY Total']
        
        st.caption(
            f"Showing {len(df_annex)} airports  •  {_t/1e6:.1f}M passengers  "
            f"•  {_i/_t*100:.1f}% international / {(_t-_i)/_t*100:.1f}% domestic"
        )
        st.info(f" **Largest Airport:** {largest_airport} ({largest_val:,.0f} pax) &nbsp;&nbsp;|&nbsp;&nbsp;  **Smallest Airport:** {smallest_airport} ({smallest_val:,.0f} pax)")
        
    st.dataframe(df_annex)

    st.subheader("Top 10 Airports by Passengers")
    df_top10 = df_annex.nlargest(10, 'FY Total')
    fig_top10 = px.bar(
        df_top10,
        x='FY Total',
        y='Airport',
        orientation='h',
        title='Top 10 Airports by Total Passengers',
        color='FY Total',
        color_continuous_scale='blues'
    )
    fig_top10.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10), yaxis={'categoryorder': 'total ascending'})
    st.plotly_chart(fig_top10, use_container_width=True)

    st.subheader("Traffic Share by Airport Category")
    df_category = df_annex.groupby('Airport Category')['FY Total'].sum().reset_index()
    fig_pie = px.pie(
        df_category,
        values='FY Total',
        names='Airport Category',
        title='Passenger Share by Airport Category'
    )
    fig_pie.update_layout(paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_pie, use_container_width=True)

    st.subheader("Airport Growth vs Decline")
    fig_growth = px.bar(
        df_annex.sort_values('Change%'),
        x='Airport',
        y='Change%',
        title='Year on Year Change % by Airport',
        color='Change%',
        color_continuous_scale='rdylgn'
    )
    fig_growth.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
    fig_growth.add_hline(y=0, line_color='white', line_dash='dash')
    st.plotly_chart(fig_growth, use_container_width=True)

    st.subheader("International vs Domestic Split by Airport")
    df_split_annex = df_annex[['Airport', 'FY Intl', 'FY Domestic']].melt(
        id_vars='Airport',
        value_vars=['FY Intl', 'FY Domestic'],
        var_name='Type',
        value_name='Passengers'
    )
    fig_split = px.bar(
        df_split_annex,
        x='Airport',
        y='Passengers',
        color='Type',
        barmode='stack',
        title='International vs Domestic Passengers by Airport',
        color_discrete_map={'FY Intl': 'red', 'FY Domestic': 'lightskyblue'}
    )
    fig_split.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_split, use_container_width=True)

    st.subheader("Airport Trend Across Years")
    all_years = ['19-20', '20-21', '21-22', '22-23', '23-24', '24-25', '25-26']
    
    # Load safe list of airports from a known complete dataset
    df_historical = load_annex3('25-26')
    if not df_historical.empty:
        airport_list = df_historical['Airport'].str.upper().tolist()
        airport_list = sorted(set(airport_list))
    else:
        airport_list = []
        
    if airport_list:
        selected_airport = st.selectbox("Select Airport", airport_list)
        norm_selected = normalize_airport_name(selected_airport)

        trend_data = []
        for yr in all_years:
            try:
                df_yr = load_annex3(yr)
                if not df_yr.empty:
                    df_yr['Norm_Airport'] = df_yr['Airport'].apply(normalize_airport_name)
                    row = df_yr[df_yr['Norm_Airport'] == norm_selected]
                    if not row.empty:
                        # sum just in case multiple matches occur
                        trend_data.append({'Financial Year': yr, 'FY Total': row['FY Total'].sum()})
            except:
                pass

        try:
            conn = sqlite3.connect("airtraffic.db")
            df_sql_trend = pd.read_sql("SELECT Airport, Cumulative_Value FROM airport_traffic WHERE Pass_Type='Total' AND Year='2026-2027'", conn)
            conn.close()
            if not df_sql_trend.empty:
                df_sql_trend['Norm_Airport'] = df_sql_trend['Airport'].apply(normalize_airport_name)
                sql_row = df_sql_trend[df_sql_trend['Norm_Airport'] == norm_selected]
                if not sql_row.empty:
                    trend_data.append({'Financial Year': '26-27', 'FY Total': sql_row['Cumulative_Value'].sum()})
        except:
            pass

        if trend_data:
            df_trend = pd.DataFrame(trend_data)
            fig_trend = px.line(
                df_trend,
                x='Financial Year',
                y='FY Total',
                title=f'{selected_airport} Passenger Trend',
                markers=True
            )
            fig_trend.update_layout(plot_bgcolor='black', paper_bgcolor='black', font_color='white', title_font_color='white', margin=dict(l=10, r=10, t=40, b=10))
            fig_trend.update_traces(marker_color='royalblue', line_color='white', line_width=5)
            st.plotly_chart(fig_trend, use_container_width=True)
