from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd
from datetime import datetime, timedelta
import os
from typing import Optional
import uvicorn
import json
import numpy as np

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI(title="Presence Management System")


app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Constants ---
LOCAL_EXCEL_PATH = "presence_data.xlsx"
OFFLINE_UPDATES_PATH = "offline_updates.json"  # store unsent updates here

GOOGLE_SHEET_ID = "18jbuEXk_qWEzUeRrm2US30qOXT4z2EoK-FcRaTTAnJ0"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def format_phone(phone):
    # Convert phone to string and prepend '0' if not present.
    phone_str = str(phone).strip()
    if not phone_str.startswith("0"):
        return "0" + phone_str
    return phone_str



# -----------------------------------------------------------------------------
# 1) Google Sheets: build service account credentials
# -----------------------------------------------------------------------------
def get_google_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        "service_account.json",  # your service account JSON
        scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds).spreadsheets()


# -----------------------------------------------------------------------------
# 2) Local Excel loading
# -----------------------------------------------------------------------------
# def load_local_excel():
#     if not os.path.exists(LOCAL_EXCEL_PATH):
#         df = pd.DataFrame(columns=[
#             "No",
#             "Nama Lengkap Peserta (Suami)",
#             "Nama Lengkap Peserta (Istri)",
#             "Nomor Handphone/ WA",
#             "Tanggal Pernikahan"
#         ])
#         df.to_excel(LOCAL_EXCEL_PATH, index=False)

#     # Specify dtype for the phone column
#     df = pd.read_excel(LOCAL_EXCEL_PATH, dtype={"Nomor Handphone/ WA": str})
#       # 3) Remove any newline or carriage-return from the cells
#     df.replace({r'[\r\n]+': ' '}, regex=True, inplace=True)
#     return df

def load_local_excel():
    if not os.path.exists(LOCAL_EXCEL_PATH):
        df = pd.DataFrame(columns=[
            "No",
            "Nama Lengkap Peserta (Suami)",
            "Nama Lengkap Peserta (Istri)",
            "Nomor Handphone/ WA",
            "Tanggal Pernikahan"
        ])
        df.to_excel(LOCAL_EXCEL_PATH, index=False)

    # 1) Read Excel as string dtype (avoids float -> string conflicts)
    df = pd.read_excel(LOCAL_EXCEL_PATH, dtype=str)

    # 2) Replace any "nan" or actual NaN with "" 
    #    (Sometimes Excel stores empty cells as np.nan, 
    #     but now the dtype is 'object'/'str', so "nan" might appear.)
    df = df.replace("nan", "", regex=False)
    df = df.fillna("")  # Double safety

    # 3) Remove newlines
    df.replace({r'[\r\n]+': ' '}, regex=True, inplace=True)

    # 4) Remove trailing ".0" if it exists in phone column
    #    e.g. "08123.0" => "08123"
    # if "Nomor Handphone/ WA" in df.columns:
    #     df["Nomor Handphone/ WA"] = df["Nomor Handphone/ WA"].apply(
    #         lambda x: x[:-2] if x.endswith(".0") else x
    #     )

    return df



# -----------------------------------------------------------------------------
# 3) Offline queue management
# -----------------------------------------------------------------------------
def store_offline_update(date_str: str, attendance_str: str, remarks_str: str = ""):
    """
    Keep only ONE entry per date in offline_updates.json.
    If the same date already exists, replace it with the new attendance total.
    """
    data = []
    if os.path.exists(OFFLINE_UPDATES_PATH):
        try:
            with open(OFFLINE_UPDATES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []

    # Convert to dict for easier "one entry per date" logic
    # e.g. { "09-04-2025": {"attendance": "3", "remarks": ""}, ... }
    offline_dict = {}
    for item in data:
        offline_dict[item["date"]] = {
            "attendance": item["attendance"],
            "remarks": item.get("remarks", "")
        }

    # Update or add the new date
    offline_dict[date_str] = {
        "attendance": attendance_str,
        "remarks": remarks_str
    }

    # Convert back to a list of dicts
    new_data = []
    for d, val in offline_dict.items():
        new_data.append({
            "date": d,
            "attendance": val["attendance"],
            "remarks": val["remarks"]
        })

    with open(OFFLINE_UPDATES_PATH, "w", encoding="utf-8") as f:
        json.dump(new_data, f, indent=2)


def replay_offline_updates(sheets_service):
    """
    Attempt to send all offline updates to Google Sheets again. 
    If successful, remove them from file.
    """
    if not os.path.exists(OFFLINE_UPDATES_PATH):
        return  # nothing to replay

    try:
        with open(OFFLINE_UPDATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        data = []

    if not data:
        return

    success_updates = []
    for item in data:
        # We set skip_offline=True so we don't get recursion
        ok = update_google_sheet(
            item["date"], 
            item["attendance"], 
            item.get("remarks", ""),
            sheets_service=sheets_service,
            skip_offline=True 
        )
        if ok:
            success_updates.append(item)

    # If all updates succeeded, remove the file
    if len(success_updates) == len(data):
        os.remove(OFFLINE_UPDATES_PATH)
    else:
        # Some might have failed, rewrite the partial failures
        fails = [x for x in data if x not in success_updates]
        with open(OFFLINE_UPDATES_PATH, "w", encoding="utf-8") as f:
            json.dump(fails, f, indent=2)


# -----------------------------------------------------------------------------
# 4) Update Google Sheet: one row per date
#    - A=Date (DD-MM-YYYY), B=Attendance, C=Remarks
#    - If row with date_str exists, update it; else append
# -----------------------------------------------------------------------------
def update_google_sheet(
    date_str: str, 
    attendance_str: str, 
    remarks_str: str = "",
    sheets_service=None,
    skip_offline=False
):
    """
    date_str like "09-04-2025"
    attendance_str like "3"
    remarks_str is optional
    skip_offline=True => do not re-queue or replay to avoid recursion
    """
    # If skip_offline=False, we do want to try replay first
    if not sheets_service:
        try:
            sheets_service = get_google_sheets_service()
        except Exception as e:
            print(f"Could not build Google Sheets service: {e}")
            # If we can't build the service, store offline
            if not skip_offline:
                store_offline_update(date_str, attendance_str, remarks_str)
            return False

    if not skip_offline:
        # Replay anything leftover from previous failures
        try:
            replay_offline_updates(sheets_service)
        except Exception as e:
            print(f"Error replaying offline updates: {e}")

    try:
        # 1) Read entire column A to see if date_str already exists
        sheet_data = sheets_service.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet1!A:A"
        ).execute()
        rows = sheet_data.get("values", [])  # e.g. [["Date"], ["09-04-2025"], ...]

        # We'll see if date_str is present
        row_index = None
        for i, row in enumerate(rows):
            # row is a list: e.g. ["09-04-2025"]
            if row and row[0] == date_str:
                row_index = i + 1  # 1-based
                break

        # Prepare 3 columns: A=Date, B=Attendance, C=Remarks
        new_values = [date_str, attendance_str, remarks_str]

        if row_index is None:
            # Not found => append at next empty row
            next_row = len(rows) + 1  # 1-based index
            sheets_service.values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"Sheet1!A{next_row}:C{next_row}",
                valueInputOption="RAW",
                body={"values": [new_values]}
            ).execute()
            print(f"[GoogleSheet] Appended row for {date_str} => attendance={attendance_str}")
        else:
            # Found => update existing row
            sheets_service.values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"Sheet1!A{row_index}:C{row_index}",
                valueInputOption="USER_ENTERED",
                body={"values": [new_values]}
            ).execute()
            print(f"[GoogleSheet] Updated row {row_index} for {date_str} => attendance={attendance_str}")
        return True

    except Exception as e:
        print(f"Error updating Google Sheet: {e}")
        if not skip_offline:
            store_offline_update(date_str, attendance_str, remarks_str)
        return False

def sync_local_excel_to_sheet(sheets_service):
    try:
        df = load_local_excel()

        # 1) Remove all newline/carriage-return characters that break the JSON
        df = df.replace({r'[\r\n]+': ' '}, regex=True)

        # Convert to list of lists, including header
        columns = list(df.columns)
        values = [columns] + df.values.tolist()

        # Clear 'Sheet2'
        sheets_service.values().clear(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet2"
        ).execute()

        # Update 'Sheet2' in RAW mode or USER_ENTERED, your choice.
        sheets_service.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet2",  # or 'Sheet2!A1'
            valueInputOption="RAW",
            body={"values": values}
        ).execute()

        print("[Sheet2] Synced local Excel to Sheet2 successfully.")
        return True

    except Exception as e:
        print(f"[Sheet2] Error syncing to Sheet2: {e}")
        return False



# -----------------------------------------------------------------------------
# 5) ROUTES
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/autocomplete")
async def autocomplete(term: str, field: str):
    df = load_local_excel()
    if field not in df.columns:
        return JSONResponse({"suggestions": []})

    # Filter data based on field & search term
    filtered_data = df[df[field].str.contains(term, case=False, na=False)]
    suggestions = filtered_data[field].tolist()
    return JSONResponse({"suggestions": suggestions[:10]})


@app.get("/api/get_person_details")
async def get_person_details(nama_suami: Optional[str] = "", nama_istri: Optional[str] = ""):
    df = load_local_excel()
    nama_suami = nama_suami.strip()
    nama_istri = nama_istri.strip()

    # Try suami+istri first
    if nama_suami and nama_istri:
        match = df[
            (df["Nama Lengkap Peserta (Suami)"] == nama_suami) &
            (df["Nama Lengkap Peserta (Istri)"] == nama_istri)
        ]
        if not match.empty:
            row = match.iloc[[0]].replace({pd.NaT: "", np.nan: ""})
            return {"success": True, "person": row.iloc[0].to_dict()}

    # Fallback suami only
    if nama_suami:
        match = df[df["Nama Lengkap Peserta (Suami)"] == nama_suami]
        if not match.empty:
            row = match.iloc[[0]].replace({pd.NaT: "", np.nan: ""})
            return {"success": True, "person": row.iloc[0].to_dict()}

    # Fallback istri only
    if nama_istri:
        match = df[df["Nama Lengkap Peserta (Istri)"] == nama_istri]
        if not match.empty:
            row = match.iloc[[0]].replace({pd.NaT: "", np.nan: ""})
            return {"success": True, "person": row.iloc[0].to_dict()}

    return {"success": False}


@app.post("/api/submit")
async def submit_presence(
    nama_suami: str = Form(...),
    nama_istri: str = Form(...),
    phone_number: str = Form(...),
    tanggal_pernikahan: str = Form(...),
    remarks: Optional[str] = Form(None)
):
    """
    - Store or update the couple in presence_data.xlsx
    - Mark 'Attend' in today's date column
    - Then get total # of attendees for that day
    - Finally, update Google Sheet with (DD-MM-YYYY, total, remarks) in columns A,B,C
    - If offline, queue the update locally (only one entry per date).
    """
    try:
        df = load_local_excel()

        # Ensure there's a 'Remarks' column
        if "Remarks" not in df.columns:
            df["Remarks"] = ""

        # We'll store the presence under an ISO col in local Excel
        today_col = datetime.now().strftime("%d-%m-%Y")  # "09-04-2025"

        # But for the Google Sheet, we want "DD-MM-YYYY"
        date_str = datetime.now().strftime("%d-%m-%Y")   # "09-04-2025"

         # Format the phone number so it always has a leading 0.
        phone_str = format_phone(phone_number)


        existing = df[
            (df["Nama Lengkap Peserta (Suami)"] == nama_suami) &
            (df["Nama Lengkap Peserta (Istri)"] == nama_istri)
        ]

        if existing.empty:
            new_row = {
                "No": len(df) + 1,
                "Nama Lengkap Peserta (Suami)": nama_suami,
                "Nama Lengkap Peserta (Istri)": nama_istri,
                "Nomor Handphone/ WA": phone_str,
                "Tanggal Pernikahan": tanggal_pernikahan,
                "Remarks": remarks or ""
            }
            if today_col not in df.columns:
                df[today_col] = ""
            new_row[today_col] = "Attend"
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            idx = existing.index[0]
            df.loc[idx, "Nomor Handphone/ WA"] = phone_str
            df.loc[idx, "Tanggal Pernikahan"] = tanggal_pernikahan
            df.loc[idx, "Remarks"] = remarks or ""

            if today_col not in df.columns:
                df[today_col] = ""
            df.loc[idx, today_col] = "Attend"


        df.to_excel(LOCAL_EXCEL_PATH, index=False)

        # Count total attends
        attendance_count = (df[today_col] == "Attend").sum()
        attendance_str = str(int(attendance_count))

        # # Update Google sheet (or queue offline if fails)
        # update_ok = update_google_sheet(date_str, attendance_str, remarks or "")
        # if update_ok:
        #     # 2) If that worked, also copy entire local Excel to "Sheet2"
        #     sheets_service = get_google_sheets_service()
        #     sync_local_excel_to_sheet(sheets_service)

        #     return JSONResponse({
        #         "success": True,
        #         "message": "Presence recorded successfully"
        #     })
        # else:
        #     # Remains offline, queued for next time
        #     return JSONResponse({
        #         "success": True, 
        #         "message": "Presence recorded locally, but offline. Will sync later."
        #     })
           # Return success immediately; no Sheet calls here
        return JSONResponse({
            "success": True,
            "message": "Presence recorded locally (no Google Sheet call)."
        })


    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------------
# Lifecycle using add_event_handler instead of @app.on_event
# ----------------------------------------------------------------

# ------------------------------
#  Lifecycle: Startup & Shutdown
# ------------------------------

def on_startup():
    """
    Called once when the server starts.
    We replay any offline updates (so they get pushed to Google if possible).
    """
    print("App starting up. Attempting to replay offline updates...")
    try:
        sheets_service = get_google_sheets_service()
        replay_offline_updates(sheets_service)  # If any
        print("Startup complete. Offline updates replayed.")
    except Exception as e:
        print(f"Startup error: {e}")

def on_shutdown():
    print("App shutting down... Attempting final data push to Google Sheets...")
    try:
        df = load_local_excel()
        today_col = datetime.now().strftime("%d-%m-%Y")
        attendance_count = (df[today_col] == "Attend").sum()
        attendance_str = str(attendance_count)

        # Gather all remarks for today's attends
        # Just combine them into one line. 
        remarks_list = df.loc[df[today_col] == "Attend", "Remarks"].tolist()
        combined_remarks = "; ".join(r for r in remarks_list if r)

        update_ok = update_google_sheet(today_col, attendance_str, combined_remarks)
        if update_ok:
            sheets_service = get_google_sheets_service()
            sync_local_excel_to_sheet(sheets_service)
            print("Shutdown: local Excel was fully synced.")
        else:
            print("Shutdown: Went offline. Data queued for next startup replay.")

    except Exception as e:
        print(f"Shutdown error: {e}")



app.add_event_handler("startup", on_startup)
app.add_event_handler("shutdown", on_shutdown)
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
