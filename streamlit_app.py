import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import re
import io
import arabic_reshaper
from bidi.algorithm import get_display

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Isra University Pharmacy Scheduler",
    page_icon="🏫",
    layout="wide"
)

# --- 1. DATA LOADING AND PARSING CONFIGURATION ---
@st.cache_data
def load_and_parse_data():
    excel_path = 'Isra University Faculty of Pharmacy Schedule 2026-2027 First-updated-15oct.xlsx'
    xl = pd.ExcelFile(excel_path)
    target_sheet = 'Untitled' if 'Untitled' in xl.sheet_names else xl.sheet_names[0]
    df_raw = xl.parse(target_sheet)

    header_row_idx = None
    for idx, row in df_raw.iterrows():
        if 'رمز المقرر' in row.values or 'إسم المقرر' in row.values:
            header_row_idx = idx
            break

    if header_row_idx is not None:
        headers = [str(h).strip() if pd.notna(h) else f"Col_{i}" for i, h in enumerate(df_raw.iloc[header_row_idx].values)]
        data_rows = df_raw.iloc[header_row_idx + 1:].copy()
        data_rows.columns = headers
    else:
        data_rows = df_raw.copy()

    cleaned_rows = []
    current_instructor = "Unknown"

    for idx, row in data_rows.iterrows():
        val_str = str(row.iloc[0])
        if "المحاضر:" in val_str or "المحاضر" in str(row.get('المحاضر', '')):
            match = re.search(r'المحاضر:\s*([^—\-]+)', val_str)
            if match:
                current_instructor = match.group(1).strip()
            elif pd.notna(row.get('المحاضر')):
                current_instructor = str(row.get('المحاضر')).strip()
            continue
        if pd.isna(row.get('رمز المقرر')) and pd.isna(row.get('إسم المقرر')):
            continue
        if str(row.get('رمز المقرر')).strip() in ['#', 'رمز المقرر', 'nan', '']:
            continue

        row_dict = row.to_dict()
        inst = row_dict.get('المحاضر')
        row_dict['المحاضر'] = current_instructor if (pd.isna(inst) or str(inst).strip() in ['', 'nan']) else str(inst).strip()
        cleaned_rows.append(row_dict)

    df_schedule = pd.DataFrame(cleaned_rows)

    rename_dict = {
        'رمز المقرر': 'Course_Code',
        'إسم المقرر': 'Course_Name',
        'نوع الشعبة': 'Section_Type',
        'نوع الدراسة': 'Study_Type',
        'النشاط': 'Activity',
        'الساعات': 'Hours',
        'القاعة': 'Room',
        'نوع التدريس': 'Teaching_Type',
        'الوقت': 'Time',
        'المحاضر': 'Instructor'
    }
    df_schedule = df_schedule.rename(columns=rename_dict)
    df_schedule = df_schedule[[c for c in rename_dict.values() if c in df_schedule.columns]]
    df_schedule['Hours'] = pd.to_numeric(df_schedule['Hours'], errors='coerce').fillna(0.0)

    # Time parsing
    parsed_data = []
    for _, row in df_schedule.iterrows():
        d, s, e = parse_time_slot(row['Time'])
        parsed_data.append({'days': d, 'start_min': s, 'end_min': e})
    df_schedule = pd.concat([df_schedule, pd.DataFrame(parsed_data)], axis=1)

    df_schedule['Workload_Hours'] = df_schedule.apply(
        lambda r: 1.5 if ('عملي' in str(r['Activity']) or 'مختبر' in str(r['Course_Name']) or 'مختبر' in str(r['Activity'])) else float(r['Hours']),
        axis=1
    )
    return df_schedule

def parse_time_slot(time_str):
    if pd.isna(time_str) or not isinstance(time_str, str):
        return set(), None, None
    clean = time_str.replace('(', '').replace(')', '').strip()
    parts = [p.strip() for p in clean.split(',')]
    days = set()
    day_mapping = {
        'حد': 'Sunday', 'خمس': 'Thursday', 'خميس': 'Thursday',
        'تن': 'Monday', 'ثن': 'Monday', 'ثلا': 'Tuesday', 'ثل': 'Tuesday',
        'ربع': 'Wednesday', 'أربع': 'Wednesday', 'سبت': 'Saturday'
    }
    for part in parts:
        if '-' in part:
            for sp in part.split('-'):
                if sp.strip() in day_mapping:
                    days.add(day_mapping[sp.strip()])
        elif part in day_mapping:
            days.add(day_mapping[part])

    times = re.findall(r'\d{1,2}:\d{2}', clean)
    if len(times) == 2:
        m1 = int(times[0].split(':')[0]) * 60 + int(times[0].split(':')[1])
        m2 = int(times[1].split(':')[0]) * 60 + int(times[1].split(':')[1])
        return days, min(m1, m2), max(m1, m2)
    return days, None, None

# --- INITIALIZE SESSION STATE ---
if 'global_df' not in st.session_state:
    st.session_state.global_df = load_and_parse_data()
    st.session_state.undo_stack = []
    st.session_state.redo_stack = []
    st.session_state.transfer_history_log = []

# --- HELPER FUNCTIONS ---
def fix_arabic_text(text):
    if not isinstance(text, str):
        return ""
    return get_display(arabic_reshaper.reshape(text))

def get_instructors():
    return sorted(st.session_state.global_df['Instructor'].dropna().unique())

def check_conflicts(schedule_df):
    conflicts = []
    for inst, group in schedule_df.groupby('Instructor'):
        rows = group.to_dict('records')
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                r1, r2 = rows[i], rows[j]
                if r1['start_min'] is not None and r2['start_min'] is not None:
                    common_days = r1['days'].intersection(r2['days'])
                    if common_days and not (r1['end_min'] <= r2['start_min'] or r2['end_min'] <= r1['start_min']):
                        conflicts.append({
                            'Instructor': inst,
                            'Course 1': f"{r1['Course_Name']} ({r1['Course_Code']})",
                            'Course 2': f"{r2['Course_Name']} ({r2['Course_Code']})",
                            'Days': ', '.join(common_days),
                            'Time 1': r1['Time'],
                            'Time 2': r2['Time']
                        })
    return pd.DataFrame(conflicts)

def generate_instructor_calendar(instructor):
    inst_df = st.session_state.global_df[st.session_state.global_df['Instructor'] == instructor]
    fig, ax = plt.subplots(figsize=(10, 5))
    days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Saturday', 'Sunday']
    day_y_indices = {day: idx for idx, day in enumerate(days_order)}

    ax.set_xlim(480, 1020)
    ax.set_ylim(-0.5, len(days_order) - 0.5)
    hours_ticks = list(range(480, 1021, 60))
    hours_labels = [f"{(h//60):02d}:{(h%60):02d}" for h in hours_ticks]
    ax.set_xticks(hours_ticks)
    ax.set_xticklabels(hours_labels, fontsize=9)
    ax.set_yticks(range(len(days_order)))
    ax.set_yticklabels(days_order, fontsize=10, fontweight='bold')
    ax.grid(True, which='both', axis='x', color='lightgray', linestyle='--', alpha=0.5)
    ax.set_axisbelow(True)

    unique_courses = inst_df['Course_Code'].dropna().unique()
    colors = list(mcolors.TABLEAU_COLORS.values())
    course_color_map = {code: colors[i % len(colors)] for i, code in enumerate(unique_courses)}

    for _, row in inst_df.iterrows():
        days = row.get('days', set())
        start_min = row.get('start_min')
        end_min = row.get('end_min')
        if start_min is not None and end_min is not None and isinstance(days, set):
            for day in days:
                if day in day_y_indices:
                    y_pos = day_y_indices[day]
                    duration = end_min - start_min
                    color = course_color_map.get(row['Course_Code'], '#3498db')
                    rect = plt.Rectangle((start_min, y_pos - 0.35), duration, 0.7, facecolor=color, edgecolor='black', alpha=0.8, zorder=3)
                    ax.add_patch(rect)
                    display_name = fix_arabic_text(row['Course_Name'])
                    room_str = fix_arabic_text(f"({row['Room']})")
                    ax.text(start_min + duration/2, y_pos, f"{display_name}\n{room_str}", ha='center', va='center', color='white', fontsize=8, fontweight='bold', zorder=4)

    plt.title(fix_arabic_text(f"Weekly Calendar - {instructor}"), fontsize=12, fontweight='bold', pad=12)
    plt.xlabel("Time (Hours)", fontsize=10)
    plt.gca().invert_yaxis()
    plt.tight_layout()
    return fig

# --- STREAMLIT UI LAYOUT ---
st.title("🏫 Isra University - Faculty of Pharmacy Scheduler & Calendar App")

instructors = get_instructors()
selected_instructor = st.selectbox("Select Instructor", instructors)

col1, col2 = st.columns([2, 1])

inst_df = st.session_state.global_df[st.session_state.global_df['Instructor'] == selected_instructor]
total_workload = inst_df['Workload_Hours'].sum()
total_standard_hours = inst_df['Hours'].sum()

with col1:
    st.markdown(f"### Timetable for: **{selected_instructor}**")
    st.info(f"**Standard Scheduled Hours:** {total_standard_hours} hrs | **Calculated Workload (Lab=1.5 hrs):** {total_workload} hrs")
    
    cols_to_show = ['Course_Code', 'Course_Name', 'Section_Type', 'Activity', 'Hours', 'Workload_Hours', 'Room', 'Time']
    st.dataframe(inst_df[cols_to_show].reset_index(drop=True), use_container_width=True)

with col2:
    st.markdown("### 📊 Global Workload Summary")
    fig, ax = plt.subplots(figsize=(6, 5))
    inst_workloads = st.session_state.global_df.groupby('Instructor')['Workload_Hours'].sum().reset_index().sort_values(by='Workload_Hours', ascending=False)
    display_instructors = [fix_arabic_text(x) for x in inst_workloads['Instructor']]
    colors = ['#1f77b4' if x != selected_instructor else '#d62728' for x in inst_workloads['Instructor']]
    ax.barh(display_instructors, inst_workloads['Workload_Hours'], color=colors)
    ax.set_title(fix_arabic_text("Instructor Workloads (Selected in Red)"), fontsize=9)
    ax.set_xlabel("Workload Hours", fontsize=8)
    plt.tight_layout()
    st.pyplot(fig)

st.markdown("---")
st.markdown("## 🗓️ Instructor Weekly Calendar Grid")
st.pyplot(generate_instructor_calendar(selected_instructor))

st.markdown("---")
st.markdown("## 🔄 Transfer Course / Reassignment & History Tracking")

t_col1, t_col2 = st.columns(2)

with t_col1:
    inst_courses = inst_df
    course_options = {f"{row['Course_Name']} ({row['Course_Code']}) - Sec: {row['Section_Type']}": idx for idx, row in inst_courses.iterrows()}
    
    selected_course_label = st.selectbox("Course to Move", options=list(course_options.keys()) if course_options else ["No courses available"])
    target_instructor = st.selectbox("Target Instructor", options=[i for i in instructors if i != selected_instructor])
    
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    
    with btn_col1:
        transfer_clicked = st.button("Transfer Course", type="primary")
    with btn_col2:
        undo_clicked = st.button("↩️ Undo")
    with btn_col3:
        redo_clicked = st.button("🔁 Redo")

    if transfer_clicked and course_options:
        selected_idx = course_options[selected_course_label]
        simulated_df = st.session_state.global_df.copy()
        course_row = simulated_df.loc[selected_idx]
        old_inst = course_row['Instructor']
        
        simulated_df.at[selected_idx, 'Instructor'] = target_instructor
        conflicts = check_conflicts(simulated_df)
        target_conflicts = conflicts[conflicts['Instructor'] == target_instructor]
        
        if not target_conflicts.empty:
            st.error(f"⚠️ CONFLICT WARNING for {target_instructor}! Cannot transfer due to overlapping schedule.")
        else:
            st.session_state.undo_stack.append((selected_idx, old_inst, target_instructor))
            st.session_state.redo_stack.clear()
            st.session_state.global_df.at[selected_idx, 'Instructor'] = target_instructor
            log_msg = f"Transferred '{course_row['Course_Name']}' from {old_inst} to {target_instructor}"
            st.session_state.transfer_history_log.append(log_msg)
            st.success(f"✅ Successfully transferred course to {target_instructor}!")
            st.rerun()

    if undo_clicked:
        if st.session_state.undo_stack:
            idx, old_inst, new_inst = st.session_state.undo_stack.pop()
            st.session_state.redo_stack.append((idx, old_inst, new_inst))
            c_name = st.session_state.global_df.at[idx, 'Course_Name']
            st.session_state.global_df.at[idx, 'Instructor'] = old_inst
            st.session_state.transfer_history_log.append(f"[UNDO] Returned '{c_name}' to {old_inst}")
            st.success(f"↩️ Undone successfully!")
            st.rerun()
        else:
            st.warning("Nothing to undo!")

    if redo_clicked:
        if st.session_state.redo_stack:
            idx, old_inst, new_inst = st.session_state.redo_stack.pop()
            st.session_state.undo_stack.append((idx, old_inst, new_inst))
            c_name = st.session_state.global_df.at[idx, 'Course_Name']
            st.session_state.global_df.at[idx, 'Instructor'] = new_inst
            st.session_state.transfer_history_log.append(f"[REDO] Moved '{c_name}' to {new_inst}")
            st.success(f"🔁 Redone successfully!")
            st.rerun()
        else:
            st.warning("Nothing to redo!")

with t_col2:
    st.markdown("### 📝 Live Action & Transfer Log")
    log_text = "\n".join([f"{i+1}. {entry}" for i, entry in enumerate(st.session_state.transfer_history_log)]) if st.session_state.transfer_history_log else "No transfers logged yet."
    st.text_area("History", value=log_text, height=150, disabled=True, label_visibility="collapsed")
    
    if st.button("📥 Export Modified Schedule to Excel"):
        export_path = "Modified_Pharmacy_Schedule_2026_2027.xlsx"
        clean_export_df = st.session_state.global_df.drop(columns=['days', 'start_min', 'end_min'], errors='ignore')
        clean_export_df.to_excel(export_path, index=False)
        with open(export_path, "rb") as f:
            st.download_button(
                label="Download Excel File",
                data=f,
                file_name="Modified_Pharmacy_Schedule_2026_2027.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
