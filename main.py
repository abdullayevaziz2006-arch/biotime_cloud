import os
import uuid
import datetime
import json
import base64
from typing import List
from fastapi import FastAPI, Depends, HTTPException, Header, status, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from cloud_database import engine, Base, get_db
import models
import schemas

# Create tables
Base.metadata.create_all(bind=engine)

try:
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE employees ADD COLUMN face_image TEXT;"))
except Exception:
    pass  # Column already exists or error handled

# Migrate terminals table for serial, model, firmware columns
for col in ["serial", "model", "firmware"]:
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE terminals ADD COLUMN {col} VARCHAR DEFAULT '';"))
            print(f"MIGRATION: Added column {col} to terminals table.")
    except Exception as e:
        pass  # Column already exists

app = FastAPI(title="BioTime Control - Cloud Super Admin Server")

# Create static directory
os.makedirs("static/updates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root_redirect():
    return RedirectResponse(url="/admin")

# Create templates directory
os.makedirs("templates", exist_ok=True)
os.makedirs("uploads/logs", exist_ok=True)
os.makedirs("uploads/backups", exist_ok=True)

templates = Jinja2Templates(directory="templates")

# Admin credentials from environment
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "admin123")


# Helper function to validate API Key and return Organization
def get_current_organization(authorization: str = Header(...), db: Session = Depends(get_db)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format. Must be Bearer <token>")
    api_key = authorization.split(" ")[1]
    org = db.query(models.Organization).filter(models.Organization.api_key == api_key).first()
    if not org:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    return org


class RedirectException(Exception):
    def __init__(self, location: str):
        self.location = location


@app.exception_handler(RedirectException)
async def redirect_exception_handler(request: Request, exc: RedirectException):
    return RedirectResponse(url=exc.location, status_code=status.HTTP_303_SEE_OTHER)


def get_current_user(request: Request, db: Session = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if not session_token:
        raise RedirectException("/login")
        
    if session_token == "super_admin_token":
        return {"role": "super_admin"}
        
    org = db.query(models.Organization).filter(models.Organization.api_key == session_token).first()
    if org:
        if not org.is_active:
            # Clear cookie if blocked
            response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
            response.delete_cookie("session_token")
            return {"role": "blocked"}
        return {"role": "client", "org": org}
        
    raise RedirectException("/login")


# ─── CLIENT API ENDPOINTS ───────────────────────────────────────────────────

@app.post("/api/v1/client/heartbeat", response_model=schemas.HeartbeatResponse)
def client_heartbeat(
    req: schemas.HeartbeatRequest,
    org: models.Organization = Depends(get_current_organization),
    db: Session = Depends(get_db)
):
    # 1. Determine license status
    license_status = "active"
    if not org.is_active:
        license_status = "blocked"
    elif org.license_expires_at and org.license_expires_at < datetime.datetime.utcnow():
        license_status = "expired"

    # 2. Sync Terminals
    for t_data in req.terminals:
        terminal = db.query(models.Terminal).filter(
            models.Terminal.organization_id == org.id,
            models.Terminal.local_terminal_id == t_data.id
        ).first()
        
        if terminal:
            terminal.name = t_data.name
            terminal.ip = t_data.ip
            terminal.port = t_data.port
            terminal.username = t_data.username
            terminal.status = t_data.status
            terminal.model = t_data.model
            terminal.firmware = t_data.firmware
            terminal.serial = t_data.serial
            terminal.last_seen = datetime.datetime.utcnow()
        else:
            new_terminal = models.Terminal(
                organization_id=org.id,
                local_terminal_id=t_data.id,
                name=t_data.name,
                ip=t_data.ip,
                port=t_data.port,
                username=t_data.username,
                status=t_data.status,
                model=t_data.model,
                firmware=t_data.firmware,
                serial=t_data.serial,
                last_seen=datetime.datetime.utcnow()
            )
            db.add(new_terminal)

    # 3. Sync Employees
    for e_data in req.unsynced_employees:
        employee = db.query(models.Employee).filter(
            models.Employee.organization_id == org.id,
            models.Employee.employee_id == e_data.employee_id
        ).first()
        
        is_active_bool = bool(e_data.is_active)
        if employee:
            employee.first_name = e_data.first_name
            employee.last_name = e_data.last_name
            employee.department = e_data.department
            employee.phone = e_data.phone
            employee.is_active = is_active_bool
            if e_data.face_image:
                employee.face_image = e_data.face_image
        else:
            new_employee = models.Employee(
                organization_id=org.id,
                employee_id=e_data.employee_id,
                first_name=e_data.first_name,
                last_name=e_data.last_name,
                department=e_data.department,
                phone=e_data.phone,
                is_active=is_active_bool,
                face_image=e_data.face_image
            )
            db.add(new_employee)

    # 4. Sync Attendance Logs
    for l_data in req.unsynced_logs:
        log_exists = db.query(models.AttendanceLog).filter(
            models.AttendanceLog.organization_id == org.id,
            models.AttendanceLog.employee_id == l_data.employee_id,
            models.AttendanceLog.event_time == l_data.event_time,
            models.AttendanceLog.event_type == l_data.event_type
        ).first()
        
        if not log_exists:
            new_log = models.AttendanceLog(
                organization_id=org.id,
                employee_id=l_data.employee_id,
                terminal_id=l_data.terminal_id,
                event_time=l_data.event_time,
                event_type=l_data.event_type,
                raw_data=l_data.raw_data
            )
            db.add(new_log)

    db.commit()

    # 5. Fetch Pending Commands
    pending_cmds = db.query(models.RemoteCommand).filter(
        models.RemoteCommand.organization_id == org.id,
        models.RemoteCommand.status == "pending"
    ).all()

    commands_payload = []
    for cmd in pending_cmds:
        commands_payload.append(schemas.PendingCommand(
            command_id=cmd.id,
            command_type=cmd.command_type,
            payload=json.loads(cmd.payload or "{}")
        ))
        # Mark as sent
        cmd.status = "sent"
        
    db.commit()

    # 6. Check for updates
    latest_version = None
    update_url = None
    release_notes = None

    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version_config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                ver_config = json.load(f)
            
            latest_ver = ver_config.get("latest_version")
            
            # Helper to compare versions
            def is_version_older(current: str, latest: str) -> bool:
                try:
                    c_parts = [int(x) for x in current.split(".")]
                    l_parts = [int(x) for x in latest.split(".")]
                    while len(c_parts) < 3: c_parts.append(0)
                    while len(l_parts) < 3: l_parts.append(0)
                    return c_parts < l_parts
                except Exception:
                    return current != latest

            if latest_ver and is_version_older(req.app_version, latest_ver):
                latest_version = latest_ver
                release_notes = ver_config.get("release_notes", "")
                if req.platform == "win7_x86":
                    update_url = ver_config.get("download_url_win7_x86")
                else:
                    update_url = ver_config.get("download_url_win64")
    except Exception:
        pass

    return schemas.HeartbeatResponse(
        status="ok",
        license_status=license_status,
        pending_commands=commands_payload,
        latest_version=latest_version,
        update_url=update_url,
        release_notes=release_notes
    )


@app.post("/isup_event")
async def isup_event(request: Request, db: Session = Depends(get_db)):
    from fastapi import Response
    content_type = request.headers.get("content-type", "")
    
    # 1. Handle Multipart Form-Data (JSON inside AccessControllerEvent field)
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        try:
            form_data = await request.form()
            event_str = form_data.get("AccessControllerEvent")
            
            if event_str:
                import json
                event_data = json.loads(event_str)
                print(f"LOG: Parsed AccessControllerEvent JSON: {json.dumps(event_data)[:300]}")
                
                device_id = event_data.get("deviceID") or event_data.get("DeviceID") or event_data.get("serialNo") or event_data.get("serialNumber")
                emp_id = None
                event_log = event_data.get("eventLog", {})
                if isinstance(event_log, dict):
                    emp_id = event_log.get("employeeNoString") or event_log.get("employeeNo")
                if not emp_id:
                    emp_id = event_data.get("employeeNoString") or event_data.get("employeeNo")
                    
                event_time = None
                if isinstance(event_log, dict):
                    event_time = event_log.get("eventTime") or event_log.get("dateTime")
                if not event_time:
                    event_time = event_data.get("eventTime") or event_data.get("dateTime")
                    
                event_type = "checkin"
                att_status = None
                if isinstance(event_log, dict):
                    att_status = event_log.get("attendanceStatus") or event_log.get("attendanceDirection")
                if not att_status:
                    att_status = event_data.get("attendanceStatus") or event_data.get("attendanceDirection")
                    
                if att_status in ["checkOut", "checkout", "out"]:
                    event_type = "checkout"
                    
                terminal = None
                if device_id:
                    terminal = db.query(models.Terminal).filter(models.Terminal.serial == device_id).first()
                    if terminal:
                        terminal.status = "online"
                        terminal.last_seen = datetime.datetime.utcnow()
                        
                        # Update ip if available
                        ip_addr = event_data.get("ipAddress") or event_data.get("ip")
                        if ip_addr:
                            terminal.ip = ip_addr
                        db.commit()
                        print(f"LOG: Terminal {terminal.name} (Local ID: {terminal.local_terminal_id}) set to online via JSON")
                        
                if emp_id and event_time and terminal:
                    event_time = event_time.replace("T", " ")
                    if "+" in event_time:
                        event_time = event_time.split("+")[0]
                    if "Z" in event_time:
                        event_time = event_time.replace("Z", "")
                    event_time = event_time.strip()
                    
                    org_id = terminal.organization_id
                    log_exists = db.query(models.AttendanceLog).filter(
                        models.AttendanceLog.organization_id == org_id,
                        models.AttendanceLog.employee_id == emp_id,
                        models.AttendanceLog.event_time == event_time,
                        models.AttendanceLog.event_type == event_type
                    ).first()
                    
                    if not log_exists:
                        new_log = models.AttendanceLog(
                            organization_id=org_id,
                            employee_id=emp_id,
                            terminal_id=terminal.local_terminal_id,
                            event_time=event_time,
                            event_type=event_type,
                            raw_data=event_str
                        )
                        db.add(new_log)
                        db.commit()
                        print(f"LOG: Saved attendance log for employee {emp_id} from JSON")
            
            xml_resp = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<ResponseStatus>\n'
                '  <requestURL>isup_event</requestURL>\n'
                '  <statusCode>1</statusCode>\n'
                '  <statusString>OK</statusString>\n'
                '</ResponseStatus>'
            )
            return Response(content=xml_resp, media_type="application/xml")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(status_code=500, content=str(e))

    # 2. Handle Raw XML Requests
    body = await request.body()
    xml_str = body.decode("utf-8", errors="ignore")
    
    print(f"LOG: /isup_event body length={len(xml_str)}, start={xml_str[:150]!r}")
    
    xml_clean = xml_str.strip()
    if not xml_clean:
        print("LOG: Empty body, returning 200 OK")
        return Response(content="OK", media_type="text/plain")
        
    try:
        import xml.etree.ElementTree as ET
        if not xml_clean.startswith("<"):
            print("LOG: Warning: body does not start with '<', returning 200 OK")
            return Response(content="OK", media_type="text/plain")
            
        root = ET.fromstring(xml_clean)
        
        # 1. Register/Heartbeat
        if root.tag == "Register" or root.find("DeviceID") is not None:
            device_id = root.findtext("DeviceID") or root.findtext("deviceID")
            device_ip = root.findtext("DeviceIP") or root.findtext("deviceIP")
            
            if device_id:
                terminal = db.query(models.Terminal).filter(models.Terminal.serial == device_id).first()
                if terminal:
                    terminal.status = "online"
                    if device_ip:
                        terminal.ip = device_ip
                    terminal.last_seen = datetime.datetime.utcnow()
                    
                    device_model = root.findtext("DeviceModel")
                    if device_model:
                        terminal.model = device_model
                    fw = root.findtext("FirmwareVersion")
                    if fw:
                        terminal.firmware = fw
                    db.commit()
            
            xml_resp = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<ResponseStatus>\n'
                '  <requestURL>isup_event</requestURL>\n'
                '  <statusCode>1</statusCode>\n'
                '  <statusString>OK</statusString>\n'
                '</ResponseStatus>'
            )
            return Response(content=xml_resp, media_type="application/xml")
            
        # 2. Attendance Event
        emp_no_tag = root.find(".//employeeNoString") or root.find(".//employeeNo")
        event_time_tag = root.find(".//eventTime") or root.find(".//dateTime") or root.find(".//time")
        device_id_tag = root.find(".//deviceID") or root.find(".//DeviceID") or root.find(".//serialNumber")
        
        if emp_no_tag is not None and event_time_tag is not None:
            emp_id = emp_no_tag.text
            event_time = event_time_tag.text
            device_id = device_id_tag.text if device_id_tag is not None else "Unknown"
            
            event_time = event_time.replace("T", " ")
            if "+" in event_time:
                event_time = event_time.split("+")[0]
            if "Z" in event_time:
                event_time = event_time.replace("Z", "")
            event_time = event_time.strip()
            
            direction = root.find(".//attendanceStatus")
            event_type = "checkin"
            if direction is not None and direction.text == "checkOut":
                event_type = "checkout"
                
            terminal = db.query(models.Terminal).filter(models.Terminal.serial == device_id).first()
            if terminal:
                org_id = terminal.organization_id
                
                log_exists = db.query(models.AttendanceLog).filter(
                    models.AttendanceLog.organization_id == org_id,
                    models.AttendanceLog.employee_id == emp_id,
                    models.AttendanceLog.event_time == event_time,
                    models.AttendanceLog.event_type == event_type
                ).first()
                
                if not log_exists:
                    new_log = models.AttendanceLog(
                        organization_id=org_id,
                        employee_id=emp_id,
                        terminal_id=terminal.local_terminal_id,
                        event_time=event_time,
                        event_type=event_type,
                        raw_data=xml_str
                    )
                    db.add(new_log)
                    
                terminal.status = "online"
                terminal.last_seen = datetime.datetime.utcnow()
                db.commit()
                
        xml_resp = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ResponseStatus>\n'
            '  <requestURL>isup_event</requestURL>\n'
            '  <statusCode>1</statusCode>\n'
            '  <statusString>OK</statusString>\n'
            '</ResponseStatus>'
        )
        return Response(content=xml_resp, media_type="application/xml")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response(status_code=500, content=str(e))


@app.post("/api/v1/client/commands/result")
def command_result(
    req: schemas.CommandResultRequest,
    org: models.Organization = Depends(get_current_organization),
    db: Session = Depends(get_db)
):
    cmd = db.query(models.RemoteCommand).filter(
        models.RemoteCommand.id == req.command_id,
        models.RemoteCommand.organization_id == org.id
    ).first()
    
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
        
    cmd.status = req.status
    cmd.response_data = req.response_data
    cmd.executed_at = datetime.datetime.utcnow()
    db.commit()
    return {"status": "ok"}


@app.post("/api/v1/client/logs/upload")
async def upload_logs(
    file: UploadFile = File(...),
    org: models.Organization = Depends(get_current_organization),
    command_id: str = Header(None, alias="Command-ID"),
    db: Session = Depends(get_db)
):
    if not command_id:
        raise HTTPException(status_code=400, detail="Missing Command-ID header")
        
    cmd = db.query(models.RemoteCommand).filter(
        models.RemoteCommand.id == command_id,
        models.RemoteCommand.organization_id == org.id
    ).first()
    
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    dest_dir = f"uploads/logs/{org.id}"
    os.makedirs(dest_dir, exist_ok=True)
    file_path = os.path.join(dest_dir, f"{command_id}_logs.zip")
    
    with open(file_path, "wb") as buffer:
        shutil_bytes = await file.read()
        buffer.write(shutil_bytes)
        
    cmd.status = "success"
    cmd.response_data = f"File uploaded successfully: {file_path}"
    cmd.executed_at = datetime.datetime.utcnow()
    db.commit()
    return {"status": "ok"}


@app.post("/api/v1/client/db/backup")
async def upload_db_backup(
    file: UploadFile = File(...),
    org: models.Organization = Depends(get_current_organization),
    command_id: str = Header(None, alias="Command-ID"),
    db: Session = Depends(get_db)
):
    if not command_id:
        raise HTTPException(status_code=400, detail="Missing Command-ID header")
        
    cmd = db.query(models.RemoteCommand).filter(
        models.RemoteCommand.id == command_id,
        models.RemoteCommand.organization_id == org.id
    ).first()
    
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    dest_dir = f"uploads/backups/{org.id}"
    os.makedirs(dest_dir, exist_ok=True)
    file_path = os.path.join(dest_dir, f"{command_id}_database.zip")
    
    with open(file_path, "wb") as buffer:
        shutil_bytes = await file.read()
        buffer.write(shutil_bytes)
        
    cmd.status = "success"
    cmd.response_data = f"Database backup uploaded successfully: {file_path}"
    cmd.executed_at = datetime.datetime.utcnow()
    db.commit()
    return {"status": "ok"}


# ─── SUPER ADMIN WEB DASHBOARD ENDPOINTS ────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def get_dashboard(request: Request, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    if user["role"] != "super_admin":
        raise RedirectException(f"/admin/organizations/{user['org'].id}")
    organizations = db.query(models.Organization).all()
    # Add count of terminals and employees
    org_list = []
    for org in organizations:
        t_count = db.query(models.Terminal).filter(models.Terminal.organization_id == org.id).count()
        e_count = db.query(models.Employee).filter(models.Employee.organization_id == org.id, models.Employee.is_active == True).count()
        l_count = db.query(models.AttendanceLog).filter(models.AttendanceLog.organization_id == org.id).count()
        
        # Determine status
        license_status = "active"
        if not org.is_active:
            license_status = "blocked"
        elif org.license_expires_at and org.license_expires_at < datetime.datetime.utcnow():
            license_status = "expired"

        org_list.append({
            "id": org.id,
            "name": org.name,
            "owner_name": org.owner_name,
            "phone": org.phone,
            "api_key": org.api_key,
            "is_active": org.is_active,
            "license_expires_at": org.license_expires_at.strftime("%Y-%m-%d") if org.license_expires_at else "Unlimited",
            "license_status": license_status,
            "terminal_count": t_count,
            "employee_count": e_count,
            "log_count": l_count
        })

    # Calculate last 7 days daily counts
    today = datetime.date.today()
    days = [today - datetime.timedelta(days=i) for i in range(6, -1, -1)]
    days_labels = [d.strftime("%d-%b") for d in days]
    
    daily_logs = []
    daily_active_emps = []
    
    for d in days:
        d_str = d.isoformat()
        logs_count = db.query(models.AttendanceLog).filter(
            models.AttendanceLog.event_time.like(f"{d_str}%")
        ).count()
        daily_logs.append(logs_count)
        
        active_emps_count = db.query(models.AttendanceLog.employee_id).filter(
            models.AttendanceLog.event_time.like(f"{d_str}%")
        ).distinct().count()
        daily_active_emps.append(active_emps_count)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request, 
            "organizations": org_list, 
            "title": "Super Admin Dashboard",
            "days_labels": days_labels,
            "daily_logs": daily_logs,
            "daily_active_emps": daily_active_emps
        }
    )


@app.post("/admin/organizations/create")
def create_organization(
    name: str = Form(...),
    owner_name: str = Form(None),
    phone: str = Form(None),
    expires_str: str = Form(None),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] != "super_admin":
        raise RedirectException("/login")
        
    expires_at = None
    if expires_str:
        try:
            expires_at = datetime.datetime.strptime(expires_str, "%Y-%m-%d")
        except ValueError:
            pass

    api_key = f"bt_{uuid.uuid4().hex}"
    new_org = models.Organization(
        name=name,
        owner_name=owner_name,
        phone=phone,
        api_key=api_key,
        license_expires_at=expires_at,
        username=username,
        password=password,
        is_active=True
    )
    db.add(new_org)
    db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/organizations/{org_id}/toggle")
def toggle_organization(
    org_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] != "super_admin":
        raise RedirectException("/login")
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    if org:
        org.is_active = not org.is_active
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/organizations/{org_id}", response_class=HTMLResponse)
def view_organization_detail(
    org_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "client" and user["org"].id != org_id:
        raise RedirectException(f"/admin/organizations/{user['org'].id}")
        
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    terminals = db.query(models.Terminal).filter(models.Terminal.organization_id == org_id).all()
    employees = db.query(models.Employee).filter(models.Employee.organization_id == org_id, models.Employee.is_active == True).all()
    logs = db.query(models.AttendanceLog).filter(models.AttendanceLog.organization_id == org_id).order_by(models.AttendanceLog.id.desc()).limit(100).all()
    commands = db.query(models.RemoteCommand).filter(models.RemoteCommand.organization_id == org_id).order_by(models.RemoteCommand.created_at.desc()).limit(50).all()

    # Calculate real stats for today
    today_str = datetime.date.today().isoformat()
    
    # Get all logs for today
    today_logs = db.query(models.AttendanceLog).filter(
        models.AttendanceLog.organization_id == org_id,
        models.AttendanceLog.event_time.like(f"{today_str}%")
    ).all()
    
    # Active employee IDs
    active_emp_ids = {e.employee_id for e in employees}
    
    # Present employees today (active only)
    present_emp_ids = {l.employee_id for l in today_logs if l.employee_id in active_emp_ids}
    present_count = len(present_emp_ids)
    
    # Absent employees today
    absent_count = len(active_emp_ids - present_emp_ids)
    
    # Late employees (whose first log's time part is after 09:00)
    first_logs = {}
    for l in today_logs:
        if l.employee_id not in active_emp_ids:
            continue
        t_part = ""
        if "T" in l.event_time:
            t_part = l.event_time.split("T")[1][:5]
        elif " " in l.event_time:
            t_part = l.event_time.split(" ")[1][:5]
            
        if l.employee_id not in first_logs or l.event_time < first_logs[l.employee_id]["time"]:
            first_logs[l.employee_id] = {"time": l.event_time, "t_part": t_part}
            
    late_count = 0
    for emp_id, info in first_logs.items():
        if info["t_part"] > "09:00":
            late_count += 1
            
    # Present non-late count
    normal_present_count = max(0, present_count - late_count)

    return templates.TemplateResponse(
        request,
        "org_detail.html",
        {
            "request": request,
            "org": org,
            "terminals": terminals,
            "employees": employees,
            "logs": logs,
            "commands": commands,
            "stats_present": normal_present_count,
            "stats_absent": absent_count,
            "stats_late": late_count,
            "is_admin": user["role"] == "super_admin"
        }
    )


@app.get("/admin/api/organizations/{org_id}/details")
def get_organization_details_json(
    org_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "client" and user["org"].id != org_id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    terminals = db.query(models.Terminal).filter(models.Terminal.organization_id == org_id).all()
    employees = db.query(models.Employee).filter(models.Employee.organization_id == org_id, models.Employee.is_active == True).all()
    commands = db.query(models.RemoteCommand).filter(models.RemoteCommand.organization_id == org_id).order_by(models.RemoteCommand.created_at.desc()).limit(50).all()

    # Calculate real stats for today
    today_str = datetime.date.today().isoformat()
    today_logs = db.query(models.AttendanceLog).filter(
        models.AttendanceLog.organization_id == org_id,
        models.AttendanceLog.event_time.like(f"{today_str}%")
    ).all()
    
    active_emp_ids = {e.employee_id for e in employees}
    present_emp_ids = {l.employee_id for l in today_logs if l.employee_id in active_emp_ids}
    present_count = len(present_emp_ids)
    absent_count = len(active_emp_ids - present_emp_ids)
    
    first_logs = {}
    for l in today_logs:
        if l.employee_id not in active_emp_ids:
            continue
        t_part = ""
        if "T" in l.event_time:
            t_part = l.event_time.split("T")[1][:5]
        elif " " in l.event_time:
            t_part = l.event_time.split(" ")[1][:5]
            
        if l.employee_id not in first_logs or l.event_time < first_logs[l.employee_id]["time"]:
            first_logs[l.employee_id] = {"time": l.event_time, "t_part": t_part}
            
    late_count = 0
    for emp_id, info in first_logs.items():
        if info["t_part"] > "09:00":
            late_count += 1
            
    normal_present_count = max(0, present_count - late_count)

    return {
        "org": {
            "id": org.id,
            "name": org.name,
            "owner_name": org.owner_name or '—',
            "phone": org.phone or '—',
            "api_key": org.api_key,
            "is_active": org.is_active,
            "license_expires_at": org.license_expires_at.strftime('%Y-%m-%d') if org.license_expires_at else 'Muddatsiz',
        },
        "terminals": [
            {
                "local_terminal_id": t.local_terminal_id,
                "name": t.name,
                "model": t.model or 'Noma\'lum',
                "serial": t.serial or 'Seriya yo\'q',
                "ip": t.ip,
                "port": t.port,
                "username": t.username,
                "status": t.status,
                "last_seen": t.last_seen.strftime('%H:%M:%S') if t.last_seen else 'Noma\'lum'
            }
            for t in terminals
        ],
        "employees": [
            {
                "employee_id": e.employee_id,
                "first_name": e.first_name,
                "last_name": e.last_name,
                "department": e.department or 'Bo\'limsiz',
                "phone": e.phone or 'Telefon yo\'q',
                "is_active": e.is_active,
                "face_image": e.face_image
            }
            for e in employees
        ],
        "commands": [
            {
                "id": c.id,
                "command_type": c.command_type,
                "payload": c.payload,
                "status": c.status,
                "response_data": c.response_data,
                "created_at": c.created_at.strftime('%H:%M:%S')
            }
            for c in commands
        ],
        "stats": {
            "present": normal_present_count,
            "absent": absent_count,
            "late": late_count
        }
    }


@app.post("/admin/organizations/{org_id}/command")
def send_command(
    org_id: int,
    command_type: str = Form(...),
    terminal_id: int = Form(None),
    query: str = Form(None),
    config_json: str = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "client":
        if user["org"].id != org_id:
            raise RedirectException(f"/admin/organizations/{user['org'].id}")
        if command_type in ["sql_query", "update_config"]:
            raise HTTPException(status_code=403, detail="Ruxsat etilmagan buyruq turi.")
            
    payload = {}
    if command_type == "reboot" and terminal_id:
        payload = {"local_terminal_id": terminal_id}
    elif command_type == "sql_query" and query:
        payload = {"query": query}
    elif command_type == "update_config" and config_json:
        try:
            payload = {"config": json.loads(config_json)}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid config JSON")

    new_cmd = models.RemoteCommand(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        command_type=command_type,
        payload=json.dumps(payload),
        status="pending"
    )
    db.add(new_cmd)
    db.commit()
    return RedirectResponse(url=f"/admin/organizations/{org_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/debug_upload")
def get_debug_upload(
    user: dict = Depends(get_current_user)
):
    from fastapi.responses import HTMLResponse
    if user["role"] == "client":
        raise HTTPException(status_code=403, detail="Ruxsat etilmagan")
    if os.path.exists("debug_upload.txt"):
        with open("debug_upload.txt", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f"<pre>{f.read()}</pre>")
    return HTMLResponse(content="No debug file found")


@app.post("/admin/organizations/{org_id}/add_employee")
async def add_employee_from_admin(
    org_id: int,
    employee_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(""),
    phone: str = Form(""),
    terminal_id: str = Form(None),
    face_image: UploadFile = File(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "client":
        raise HTTPException(status_code=403, detail="Ruxsat etilmagan")

    # 1. Read face image content once and cache it
    file_content = None
    if face_image and face_image.filename:
        try:
            file_content = await face_image.read()
        except Exception as e:
            print(f"Failed to read face_image: {e}")

    # Debug details
    debug_info = f"""
    --- add_employee_from_admin Debug ---
    org_id: {org_id}
    employee_id: {employee_id}
    first_name: {first_name}
    last_name: {last_name}
    department: {department}
    phone: {phone}
    terminal_id raw: {terminal_id} (type: {type(terminal_id)})
    face_image raw: {face_image}
    """
    if face_image:
        debug_info += f"face_image.filename: '{face_image.filename}'\n"
        if file_content:
            debug_info += f"face_image content length: {len(file_content)} bytes\n"
        else:
            debug_info += "face_image content is empty\n"
    else:
        debug_info += "face_image is None\n"
        
    with open("debug_upload.txt", "w", encoding="utf-8") as f:
        f.write(debug_info)

    # 2. Convert terminal_id to int safely
    term_id_int = None
    if terminal_id and terminal_id.strip() and terminal_id.strip() != "None" and terminal_id.strip() != "null":
        try:
            term_id_int = int(terminal_id.strip())
        except ValueError:
            pass

    # 3. Base64 encode face image from cached bytes
    face_b64 = None
    if file_content:
        face_b64 = base64.b64encode(file_content).decode("utf-8")

    # 4. Save/Update in Cloud Database
    emp = db.query(models.Employee).filter(
        models.Employee.organization_id == org_id,
        models.Employee.employee_id == employee_id
    ).first()

    if emp:
        emp.first_name = first_name
        emp.last_name = last_name
        emp.department = department
        emp.phone = phone
        emp.is_active = True
        if face_b64:
            emp.face_image = face_b64
    else:
        new_emp = models.Employee(
            organization_id=org_id,
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
            department=department,
            phone=phone,
            face_image=face_b64,
            is_active=True
        )
        db.add(new_emp)

    # 5. Create a RemoteCommand for local sync
    payload = {
        "employee_id": employee_id,
        "first_name": first_name,
        "last_name": last_name,
        "department": department,
        "phone": phone,
        "face_image": face_b64,
        "terminal_id": term_id_int
    }

    new_cmd = models.RemoteCommand(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        command_type="add_employee",
        payload=json.dumps(payload),
        status="pending"
    )
    db.add(new_cmd)
    db.commit()

    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/organizations/{org_id}/employees/{employee_id}/delete")
def delete_employee_from_admin(
    org_id: int,
    employee_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "client":
        raise HTTPException(status_code=403, detail="Ruxsat etilmagan")

    # 1. Delete from Cloud Database
    emp = db.query(models.Employee).filter(
        models.Employee.organization_id == org_id,
        models.Employee.employee_id == employee_id
    ).first()

    if emp:
        db.delete(emp)
        db.commit()

    # 2. Create RemoteCommand to delete from client and terminal
    payload = {
        "employee_id": employee_id
    }

    new_cmd = models.RemoteCommand(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        command_type="delete_employee",
        payload=json.dumps(payload),
        status="pending"
    )
    db.add(new_cmd)
    db.commit()

    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/organizations/{org_id}/add_terminal")
def add_terminal_from_admin(
    org_id: int,
    local_terminal_id: int = Form(...),
    name: str = Form(...),
    ip: str = Form(...),
    port: int = Form(80),
    username: str = Form("admin"),
    password: str = Form(...),
    conn_type: str = Form("isapi"),
    serial: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if user["role"] == "client":
        raise HTTPException(status_code=403, detail="Ruxsat etilmagan")

    # 1. Save/Update in Cloud Database
    term = db.query(models.Terminal).filter(
        models.Terminal.organization_id == org_id,
        models.Terminal.local_terminal_id == local_terminal_id
    ).first()

    if term:
        term.name = name
        term.ip = ip
        term.port = port
        term.username = username
        term.serial = serial
        term.status = "offline"
    else:
        new_term = models.Terminal(
            organization_id=org_id,
            local_terminal_id=local_terminal_id,
            name=name,
            ip=ip,
            port=port,
            username=username,
            serial=serial,
            status="offline"
        )
        db.add(new_term)

    # 2. Create a RemoteCommand for local sync
    payload = {
        "local_terminal_id": local_terminal_id,
        "name": name,
        "ip": ip,
        "port": port,
        "username": username,
        "password": password,
        "conn_type": conn_type,
        "serial": serial,
        "use_https": 0,
        "is_attendance": 1,
        "attendance_direction": "all"
    }

    new_cmd = models.RemoteCommand(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        command_type="add_terminal",
        payload=json.dumps(payload),
        status="pending"
    )
    db.add(new_cmd)
    db.commit()

    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/admin/downloads/logs/{command_id}")
def download_command_logs(command_id: str, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    cmd = db.query(models.RemoteCommand).filter(models.RemoteCommand.id == command_id).first()
    if not cmd or "File uploaded successfully" not in cmd.response_data:
        raise HTTPException(status_code=404, detail="Log file not found")
        
    if user["role"] == "client" and user["org"].id != cmd.organization_id:
        raise HTTPException(status_code=403, detail="Ruxsat etilmagan")
        
    path = cmd.response_data.replace("File uploaded successfully: ", "").strip()
    if os.path.exists(path):
        return FileResponse(path, filename=f"logs_{command_id}.zip")
    raise HTTPException(status_code=404, detail="File missing on disk")


@app.get("/admin/downloads/backup/{command_id}")
def download_command_backup(command_id: str, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    cmd = db.query(models.RemoteCommand).filter(models.RemoteCommand.id == command_id).first()
    if not cmd or "Database backup uploaded successfully" not in cmd.response_data:
        raise HTTPException(status_code=404, detail="Backup file not found")
        
    if user["role"] == "client" and user["org"].id != cmd.organization_id:
        raise HTTPException(status_code=403, detail="Ruxsat etilmagan")
        
    path = cmd.response_data.replace("Database backup uploaded successfully: ", "").strip()
    if os.path.exists(path):
        return FileResponse(path, filename=f"database_{command_id}.zip")
    raise HTTPException(status_code=404, detail="File missing on disk")


# ─── LOGIN / LOGOUT ENDPOINTS ───────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if username == ADMIN_USER and password == ADMIN_PASS:
        response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie("session_token", "super_admin_token", httponly=True)
        return response
        
    org = db.query(models.Organization).filter(models.Organization.username == username).first()
    if org and org.password == password:
        if not org.is_active:
            return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Tashkilot litsenziyasi bloklangan!"})
        response = RedirectResponse(url=f"/admin/organizations/{org.id}", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie("session_token", org.api_key, httponly=True)
        return response
        
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Login yoki parol noto'g'ri!"})


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_token")
    return response
