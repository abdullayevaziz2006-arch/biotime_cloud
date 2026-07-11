import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from cloud_database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    owner_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    api_key = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    license_expires_at = Column(DateTime, nullable=True)
    username = Column(String, unique=True, index=True, nullable=True)
    password = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    terminals = relationship("Terminal", back_populates="organization", cascade="all, delete-orphan")
    employees = relationship("Employee", back_populates="organization", cascade="all, delete-orphan")
    attendance_logs = relationship("AttendanceLog", back_populates="organization", cascade="all, delete-orphan")
    commands = relationship("RemoteCommand", back_populates="organization", cascade="all, delete-orphan")


class Terminal(Base):
    __tablename__ = "terminals"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    local_terminal_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    port = Column(Integer, default=80)
    username = Column(String, default="admin")
    status = Column(String, default="offline")
    model = Column(String, default="")
    firmware = Column(String, default="")
    serial = Column(String, default="")
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)

    organization = relationship("Organization", back_populates="terminals")

    __table_args__ = (
        UniqueConstraint("organization_id", "local_terminal_id", name="uq_org_terminal"),
    )


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    employee_id = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    department = Column(String, default="")
    phone = Column(String, default="")
    is_active = Column(Boolean, default=True)
    face_image = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    organization = relationship("Organization", back_populates="employees")

    __table_args__ = (
        UniqueConstraint("organization_id", "employee_id", name="uq_org_employee"),
    )


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    employee_id = Column(String, nullable=False)
    terminal_id = Column(Integer, nullable=False)  # local_terminal_id
    event_time = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    raw_data = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    organization = relationship("Organization", back_populates="attendance_logs")

    __table_args__ = (
        UniqueConstraint("organization_id", "employee_id", "event_time", "event_type", name="uq_org_attendance"),
    )


class RemoteCommand(Base):
    __tablename__ = "remote_commands"

    id = Column(String, primary_key=True, index=True)  # UUID or custom string ID
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    command_type = Column(String, nullable=False)  # reboot, upload_logs, backup_db, sql_query, update_config
    payload = Column(Text, default="{}")  # JSON string
    status = Column(String, default="pending")  # pending, sent, success, failed
    response_data = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="commands")
