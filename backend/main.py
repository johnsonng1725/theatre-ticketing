import os
import json
import base64
import secrets
import logging
import io
import urllib.request
from datetime import datetime, date

from fastapi import FastAPI, HTTPException, Depends, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()

from database import get_db, engine
import models
import schemas

# Create tables on startup
models.Base.metadata.create_all(bind=engine)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DASHBOARD_KEY    = os.environ.get("DASHBOARD_KEY",  "admin321")
FINANCE_KEY      = os.environ.get("FINANCE_KEY",    "finance123")
SCANNER_KEY      = os.environ.get("SCANNER_KEY",    "admin123")
BACKSTAGE_KEY    = os.environ.get("BACKSTAGE_KEY",  "admin")
_raw_origins     = os.environ.get("CORS_ORIGINS", "*")
CORS_ORIGINS     = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

BACKEND_URL      = os.environ.get("BACKEND_URL",    "")   # e.g. https://theatre-ticketing-api.onrender.com

# ── Brevo email config ────────────────────────────────────────────────────────
# Sign up free at brevo.com → SMTP & API → API Keys → create key.
# BREVO_FROM_EMAIL: the sender email (your Gmail is fine, no domain needed)
# BREVO_FROM_NAME:  display name shown in inbox (e.g. "MCKL Theatre")
BREVO_API_KEY    = os.environ.get("BREVO_API_KEY",    "")
BREVO_FROM_EMAIL = os.environ.get("BREVO_FROM_EMAIL", os.environ.get("SMTP_FROM", ""))
BREVO_FROM_NAME  = os.environ.get("BREVO_FROM_NAME",  "Theatre Booking")

# ── Default event settings ────────────────────────────────────────────────────
# These are used when no override exists in the database.
SETTING_DEFAULTS = {
    "event_name":         "Immersive Theatre Experience",
    "event_subtitle":     "Reserve Your Place",
    "event_description":  "Complete the form, pay, and upload your receipt to confirm your booking.",
    "show_dates":         "2026-04-19,2026-04-26",   # comma-separated ISO dates
    "show_times":         "4.00pm-6.30pm",
    "early_bird_price":   "25",
    "standard_price":     "30",
    "early_bird_limit":   "30",
    "standard_limit":     "",    # empty = unlimited
    "total_capacity":     "100",
    "ticket_types_json":  "",    # JSON array [{name,price,limit}] — overrides legacy type fields
    "show_dates_json":    "",    # JSON array [{date,time}] — overrides show_dates + show_times
    "duitnow_name":       "YOUR NAME / ORGANISATION",
    "duitnow_id":         "01X-XXX XXXX",
    "duitnow_qr":         "",    # base64 data URL for DuitNow QR image
    "contact_name":       "",    # name shown in the "if any issue" notice on the booking page
    "contact_phone":      "",    # phone shown in the "if any issue" notice on the booking page
}


def get_all_settings(db) -> dict:
    """Return merged dict: defaults overridden by DB values."""
    rows = db.query(models.Setting).all()
    result = dict(SETTING_DEFAULTS)
    for row in rows:
        result[row.key] = row.value
    return result


# ── Email helpers ─────────────────────────────────────────────────────────────

def _generate_qr_png_bytes(ticket_id: str) -> bytes:
    """Return raw PNG bytes of the QR code. Returns b'' on failure."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=8,
            border=2,
        )
        qr.add_data(ticket_id)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"QR generation failed: {e}")
        return b""


def _format_date(iso_date: str) -> str:
    """Convert '2026-04-19' → 'Sunday, 19 April 2026, 4.00pm-6.30pm'."""
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%A, %-d %B %Y")
    except Exception:
        return iso_date


def _build_email_html(ticket, settings: dict, qr_url: str) -> str:
    """Build a beautiful HTML confirmation email."""
    event_name  = settings.get("event_name", "Theatre Event")
    show_date   = _format_date(ticket.show_date)
    qty_label   = f'{ticket.quantity} ticket{"s" if ticket.quantity > 1 else ""}'

    qr_img_html = (
        f'<img src="{qr_url}" alt="Entry QR Code" '
        'width="200" height="200" style="display:block;margin:0 auto;" />'
        if qr_url
        else '<p style="text-align:center;color:#888;font-size:13px;">QR code unavailable</p>'
    )

    receipt_notice = "" if ticket.payment_status != "pending" else """
        <tr>
          <td style="padding:0 40px 24px;background:#ffffff;">
            <div style="background:#fff7ed;border-left:4px solid #ea6d0a;border-radius:6px;padding:14px 18px;">
              <p style="color:#7a3500;font-size:13px;margin:0;line-height:1.5;">
                <strong>⚠ Receipt not yet uploaded.</strong><br>
                Please upload your payment receipt via the registration page to fully confirm your booking.
              </p>
            </div>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Booking Confirmed — {event_name}</title>
</head>
<body style="margin:0;padding:0;background:#f0eef8;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0eef8;padding:40px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:14px;overflow:hidden;max-width:560px;width:100%;border:1px solid #ddd8f0;">

        <!-- ── Header ── -->
        <tr>
          <td style="background:linear-gradient(135deg,#2d1b69 0%,#7c3aed 100%);padding:36px 40px;text-align:center;">
            <p style="color:#e9d8ff;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin:0 0 10px;">
              ✓ &nbsp; Booking Confirmed
            </p>
            <h1 style="color:#ffffff;font-size:26px;margin:0;font-weight:700;line-height:1.3;">
              {event_name}
            </h1>
          </td>
        </tr>

        <!-- ── Greeting ── -->
        <tr>
          <td style="padding:30px 40px 12px;background:#ffffff;">
            <p style="color:#1a1035;font-size:16px;margin:0 0 8px;">
              Hello, <strong style="color:#1a1035;">{ticket.name}</strong> 👋
            </p>
            <p style="color:#444466;font-size:14px;margin:0;line-height:1.6;">
              Your seat is reserved. Show the QR code below at the entrance — the staff will scan it to check you in.
            </p>
          </td>
        </tr>

        <!-- ── Ticket details card ── -->
        <tr>
          <td style="padding:16px 40px 24px;background:#ffffff;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#f7f4ff;border-radius:10px;border:1px solid #d4c8f5;">
              <tr>
                <td style="padding:20px 24px;">
                  <table width="100%" cellpadding="7" cellspacing="0">
                    <tr>
                      <td style="color:#6b5b9e;font-size:11px;text-transform:uppercase;letter-spacing:1px;width:38%;vertical-align:top;">
                        Show Date
                      </td>
                      <td style="color:#1a1035;font-size:14px;font-weight:700;">
                        {show_date}
                      </td>
                    </tr>
                    <tr>
                      <td style="color:#6b5b9e;font-size:11px;text-transform:uppercase;letter-spacing:1px;vertical-align:top;">
                        Ticket Type
                      </td>
                      <td style="color:#1a1035;font-size:14px;font-weight:700;">
                        {ticket.ticket_type}
                      </td>
                    </tr>
                    <tr>
                      <td style="color:#6b5b9e;font-size:11px;text-transform:uppercase;letter-spacing:1px;vertical-align:top;">
                        Quantity
                      </td>
                      <td style="color:#1a1035;font-size:14px;font-weight:700;">
                        {qty_label}
                      </td>
                    </tr>
                    <tr>
                      <td style="color:#6b5b9e;font-size:11px;text-transform:uppercase;letter-spacing:1px;vertical-align:top;">
                        Booking ID
                      </td>
                      <td style="color:#5b3fb5;font-size:12px;font-family:'Courier New',monospace;word-break:break-all;">
                        {ticket.ticket_id}
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- ── QR Code ── -->
        <tr>
          <td style="padding:0 40px 28px;text-align:center;background:#ffffff;">
            <p style="color:#6b5b9e;font-size:11px;text-transform:uppercase;letter-spacing:2px;margin:0 0 14px;font-weight:600;">
              Scan at Entrance
            </p>
            <div style="background:#ffffff;border:2px solid #d4c8f5;border-radius:12px;padding:16px;display:inline-block;">
              {qr_img_html}
            </div>
            <p style="color:#555577;font-size:12px;margin:12px 0 0;">
              Screenshot or print this email and bring it to the venue.
            </p>
          </td>
        </tr>

        <!-- ── Receipt notice (if pending) ── -->
        {receipt_notice}

        <!-- ── Footer ── -->
        <tr>
          <td style="background:#f7f4ff;border-top:1px solid #ddd8f0;padding:20px 40px;text-align:center;">
            <p style="color:#6b5b9e;font-size:12px;margin:0;line-height:1.5;">
              This is an automated confirmation — please do not reply to this email.<br>
              See you at the show! 🎭
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send_ticket_email(ticket, settings: dict) -> None:
    """
    Send a booking confirmation email via Brevo HTTP API.
    Non-blocking: any error is logged but NOT re-raised,
    so registration always succeeds even if email fails.
    """
    if not BREVO_API_KEY or not BREVO_FROM_EMAIL:
        logger.info(
            "Email not configured (BREVO_API_KEY/BREVO_FROM_EMAIL not set) — "
            "skipping confirmation email for %s.", ticket.ticket_id
        )
        return

    try:
        # Build a public HTTPS URL for the QR image — email clients fetch it directly.
        # This avoids CID inline attachments which Brevo's REST API doesn't support.
        qr_url     = f"{BACKEND_URL}/api/ticket/{ticket.ticket_id}/qr" if BACKEND_URL else ""
        html_body  = _build_email_html(ticket, settings, qr_url)
        event_name = settings.get("event_name", "Theatre Event")

        payload = json.dumps({
            "sender":      {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
            "to":          [{"email": ticket.email, "name": ticket.name}],
            "subject":     f"🎭 Booking Confirmed — {event_name}",
            "htmlContent": html_body,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={
                "api-key":      BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept":       "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        logger.info(
            "Confirmation email sent via Brevo → %s (messageId: %s)",
            ticket.email, result.get("messageId")
        )

    except Exception as exc:
        logger.error(
            "Failed to send confirmation email to %s: %s",
            ticket.email, exc
        )


# ── Audit log helper ──────────────────────────────────────────────────────────

def _log_action(db: Session, role: str, action: str, detail: str) -> None:
    """Insert one audit-log row. Failures are swallowed so they never break the main flow."""
    try:
        entry = models.AuditLog(role=role, action=action, detail=detail)
        db.add(entry)
        db.commit()
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Theatre Ticketing API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

def verify_dashboard(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    """Full admin access — required for write operations (edit, delete, settings)."""
    if x_admin_key != DASHBOARD_KEY:
        raise HTTPException(status_code=401, detail="Invalid dashboard key")

def verify_backstage(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    """Audit log access — accepts dashboard key or dedicated backstage key."""
    if x_admin_key not in (DASHBOARD_KEY, BACKSTAGE_KEY):
        raise HTTPException(status_code=401, detail="Invalid key")

def verify_any_admin(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    """Read-only admin access — accepts dashboard, finance, and scanner keys."""
    if x_admin_key not in (DASHBOARD_KEY, FINANCE_KEY, SCANNER_KEY):
        raise HTTPException(status_code=401, detail="Invalid key")

def verify_scanner(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    """Required by scanner route — check-in only."""
    if x_admin_key != SCANNER_KEY:
        raise HTTPException(status_code=401, detail="Invalid scanner key")


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/ticket/{ticket_id}/qr")
def get_ticket_qr(ticket_id: str):
    """Public endpoint — generates and serves the QR code PNG for a ticket.
    Used by confirmation emails so the image is fetched via HTTPS (no CID needed)."""
    png = _generate_qr_png_bytes(ticket_id)
    if not png:
        raise HTTPException(status_code=404, detail="QR generation failed")
    return Response(content=png, media_type="image/png")


@app.get("/api/admin/ping")
def admin_ping(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
    db: Session = Depends(get_db),
):
    """Key verification — returns role so the frontend knows what access level was granted."""
    if x_admin_key == DASHBOARD_KEY:
        _log_action(db, "Admin", "login", "Logged in as Admin (full access)")
        return {"ok": True, "role": "dashboard"}
    if x_admin_key == FINANCE_KEY:
        _log_action(db, "Finance", "login", "Logged in as Finance (view only)")
        return {"ok": True, "role": "finance"}
    if x_admin_key == SCANNER_KEY:
        _log_action(db, "Scanner", "login", "Logged in as Scanner (view only)")
        return {"ok": True, "role": "scanner"}
    if x_admin_key == BACKSTAGE_KEY:
        _log_action(db, "Admin", "login", "Logged in to Backstage")
        return {"ok": True, "role": "backstage"}
    raise HTTPException(status_code=401, detail="Invalid key")


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    """Public — returns all event settings (used by registration page on load)."""
    return get_all_settings(db)


@app.put(
    "/api/admin/settings",
    dependencies=[Depends(verify_dashboard)],
)
def update_settings(payload: dict, db: Session = Depends(get_db)):
    """Save updated event settings (dashboard key required)."""
    allowed_keys = set(SETTING_DEFAULTS.keys())
    changed = []
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        row = db.query(models.Setting).filter(models.Setting.key == key).first()
        if row:
            if row.value != str(value):
                changed.append(f"{key}='{value}'")
            row.value = str(value)
        else:
            db.add(models.Setting(key=key, value=str(value)))
            changed.append(f"{key}='{value}'")
    db.commit()
    if changed:
        _log_action(db, "Admin", "update_settings", "Updated settings: " + ", ".join(changed))
    return get_all_settings(db)


def _early_bird_sold(db: Session, show_date: str) -> int:
    """Return total Early Bird tickets sold (sum of quantities) for a show date."""
    return _type_sold(db, "Early Bird", show_date)


def _type_sold(db: Session, ticket_type: str, show_date: str) -> int:
    """Return tickets sold (sum of quantities) for any ticket type + show date."""
    result = (
        db.query(func.sum(models.Ticket.quantity))
        .filter(
            models.Ticket.show_date   == show_date,
            models.Ticket.ticket_type == ticket_type,
        )
        .scalar()
    )
    return result or 0


def _total_sold(db: Session, show_date: str) -> int:
    """Return total tickets sold (all types, sum of quantities) for a show date."""
    result = (
        db.query(func.sum(models.Ticket.quantity))
        .filter(models.Ticket.show_date == show_date)
        .scalar()
    )
    return result or 0


@app.get("/api/availability")
def get_availability(db: Session = Depends(get_db)):
    """Public — returns Early Bird and total capacity info per show date."""
    settings = get_all_settings(db)
    # Prefer show_dates_json (per-date rows) over legacy comma-separated show_dates
    _sdj = settings.get("show_dates_json", "")
    if _sdj:
        try:
            _date_defs = json.loads(_sdj)
            show_dates = [d["date"].strip() for d in _date_defs if d.get("date", "").strip()]
        except Exception:
            show_dates = [d.strip() for d in settings["show_dates"].split(",") if d.strip()]
    else:
        show_dates = [d.strip() for d in settings["show_dates"].split(",") if d.strip()]
    eb_limit        = int(settings["early_bird_limit"])
    total_capacity  = int(settings.get("total_capacity", "100"))
    result = {}
    for date in show_dates:
        eb_sold         = _early_bird_sold(db, date)
        total_sold      = _total_sold(db, date)
        eb_remaining    = max(0, eb_limit - eb_sold)
        total_remaining = max(0, total_capacity - total_sold)
        result[date] = {
            "early_bird_sold":      eb_sold,
            "early_bird_remaining": eb_remaining,
            "early_bird_sold_out":  eb_sold >= eb_limit,
            "total_sold":           total_sold,
            "total_remaining":      total_remaining,
            "total_sold_out":       total_sold >= total_capacity,
        }
    return result


@app.post("/api/register", response_model=schemas.TicketResponse, status_code=201)
def register_ticket(
    ticket: schemas.TicketCreate,
    db: Session = Depends(get_db),
):
    # Fetch settings once (needed for Early Bird check + email)
    settings = get_all_settings(db)

    # Enforce total venue capacity first
    total_capacity  = int(settings.get("total_capacity", "100"))
    total_sold      = _total_sold(db, ticket.show_date)
    total_remaining = max(0, total_capacity - total_sold)
    if ticket.quantity > total_remaining:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only {total_remaining} ticket(s) remaining for this date. "
                f"Please reduce your quantity."
            ),
        )

    # Enforce per-type capacity limits
    types_json = settings.get("ticket_types_json", "")
    if types_json:
        # Dynamic ticket types — check limit from ticket_types_json
        try:
            type_defs = json.loads(types_json)
            tdef = next((t for t in type_defs if t["name"] == ticket.ticket_type), None)
            if tdef and tdef.get("limit"):
                t_limit     = int(tdef["limit"])
                t_sold      = _type_sold(db, ticket.ticket_type, ticket.show_date)
                t_remaining = max(0, t_limit - t_sold)
                if ticket.quantity > t_remaining:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Only {t_remaining} {ticket.ticket_type} ticket(s) remaining for this date."
                        ),
                    )
        except (ValueError, KeyError):
            pass  # malformed JSON — skip limit check
    else:
        # Legacy mode — Early Bird limit only
        if ticket.ticket_type == "Early Bird":
            eb_limit     = int(settings.get("early_bird_limit", "30") or "30")
            eb_sold      = _early_bird_sold(db, ticket.show_date)
            eb_remaining = max(0, eb_limit - eb_sold)
            if ticket.quantity > eb_remaining:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Only {eb_remaining} Early Bird ticket(s) remaining for this date. "
                        f"Please reduce your quantity or choose Standard."
                    ),
                )
        # Standard limit (if set)
        std_limit_str = settings.get("standard_limit", "")
        if std_limit_str and ticket.ticket_type == "Standard":
            std_limit     = int(std_limit_str)
            std_sold      = _type_sold(db, "Standard", ticket.show_date)
            std_remaining = max(0, std_limit - std_sold)
            if ticket.quantity > std_remaining:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Only {std_remaining} Standard ticket(s) remaining for this date."
                    ),
                )

    ticket_id = secrets.token_urlsafe(16)
    db_ticket = models.Ticket(
        ticket_id        = ticket_id,
        name             = ticket.name,
        email            = ticket.email,
        phone            = ticket.phone,
        ticket_type      = ticket.ticket_type,
        show_date        = ticket.show_date,
        quantity         = ticket.quantity,
        receipt_data     = ticket.receipt_data,
        receipt_filename = ticket.receipt_filename,
        payment_status   = "receipt_uploaded" if ticket.receipt_data else "pending",
    )
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)

    # ── Send confirmation email (non-blocking) ────────────────────────────────
    _send_ticket_email(db_ticket, settings)

    return db_ticket


# ---------------------------------------------------------------------------
# Admin routes  (require X-Admin-Key header)
# ---------------------------------------------------------------------------

@app.get(
    "/api/admin/tickets",
    response_model=schemas.TicketListResponse,
    dependencies=[Depends(verify_any_admin)],
)
def get_tickets(db: Session = Depends(get_db)):
    tickets = (
        db.query(models.Ticket)
        .order_by(models.Ticket.created_at.desc())
        .all()
    )
    total_tickets    = sum(t.quantity for t in tickets)
    checked_in_count = sum(1 for t in tickets if t.checked_in)
    return schemas.TicketListResponse(
        total         = len(tickets),
        total_tickets = total_tickets,
        checked_in    = checked_in_count,
        tickets       = tickets,
    )


@app.get(
    "/api/admin/receipt/{ticket_id}",
    dependencies=[Depends(verify_any_admin)],
)
def get_receipt(ticket_id: str, db: Session = Depends(get_db)):
    """Return the base64 receipt data for a single ticket."""
    ticket = (
        db.query(models.Ticket)
        .filter(models.Ticket.ticket_id == ticket_id)
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    if not ticket.receipt_data:
        raise HTTPException(status_code=404, detail="No receipt uploaded for this ticket.")
    return {
        "ticket_id":       ticket.ticket_id,
        "receipt_data":    ticket.receipt_data,
        "receipt_filename": ticket.receipt_filename,
    }


@app.patch(
    "/api/admin/tickets/{ticket_id}",
    response_model=schemas.TicketResponse,
    dependencies=[Depends(verify_dashboard)],
)
def update_ticket(
    ticket_id: str,
    update: schemas.TicketUpdate,
    db: Session = Depends(get_db),
):
    """Edit any field on an existing ticket (dashboard key required)."""
    ticket = (
        db.query(models.Ticket)
        .filter(models.Ticket.ticket_id == ticket_id)
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    changes = []
    if update.name is not None and update.name.strip() != ticket.name:
        changes.append(f"name: '{ticket.name}' → '{update.name.strip()}'")
        ticket.name = update.name.strip()
    if update.email is not None and update.email.strip().lower() != ticket.email:
        changes.append(f"email: '{ticket.email}' → '{update.email.strip().lower()}'")
        ticket.email = update.email.strip().lower()
    if update.phone is not None and update.phone.strip() != ticket.phone:
        changes.append(f"phone: '{ticket.phone}' → '{update.phone.strip()}'")
        ticket.phone = update.phone.strip()
    if update.ticket_type is not None and update.ticket_type != ticket.ticket_type:
        changes.append(f"ticket_type: '{ticket.ticket_type}' → '{update.ticket_type}'")
        ticket.ticket_type = update.ticket_type
    if update.show_date is not None and update.show_date != ticket.show_date:
        changes.append(f"show_date: '{ticket.show_date}' → '{update.show_date}'")
        ticket.show_date = update.show_date
    if update.quantity is not None and update.quantity != ticket.quantity:
        changes.append(f"quantity: {ticket.quantity} → {update.quantity}")
        ticket.quantity = update.quantity
    if update.payment_status is not None and update.payment_status != ticket.payment_status:
        changes.append(f"payment_status: '{ticket.payment_status}' → '{update.payment_status}'")
        ticket.payment_status = update.payment_status

    db.commit()
    db.refresh(ticket)

    detail = f"Edited ticket for {ticket.name} (ID: {ticket_id[:8]}…)"
    if changes:
        detail += " — " + ", ".join(changes)
    _log_action(db, "Admin", "edit_ticket", detail)

    return ticket


@app.delete(
    "/api/admin/tickets/{ticket_id}",
    dependencies=[Depends(verify_dashboard)],
    status_code=204,
)
def delete_ticket(ticket_id: str, db: Session = Depends(get_db)):
    """Permanently delete a ticket registration (dashboard key required)."""
    ticket = (
        db.query(models.Ticket)
        .filter(models.Ticket.ticket_id == ticket_id)
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    detail = (
        f"Deleted ticket for {ticket.name} ({ticket.email}) — "
        f"{ticket.show_date}, {ticket.ticket_type} x{ticket.quantity} "
        f"(ID: {ticket_id[:8]}…)"
    )
    db.delete(ticket)
    db.commit()
    _log_action(db, "Admin", "delete_ticket", detail)
    return Response(status_code=204)


@app.post(
    "/api/admin/checkin",
    response_model=schemas.CheckinResponse,
    dependencies=[Depends(verify_scanner)],
)
def checkin_ticket(
    checkin: schemas.CheckinRequest,
    db: Session = Depends(get_db),
):
    ticket = (
        db.query(models.Ticket)
        .filter(models.Ticket.ticket_id == checkin.ticket_id)
        .first()
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    if ticket.checked_in:
        time_str = (
            ticket.checked_in_at.strftime("%H:%M:%S")
            if ticket.checked_in_at
            else "unknown time"
        )
        raise HTTPException(
            status_code=409,
            detail=f"Ticket already checked in at {time_str}.",
        )

    ticket.checked_in    = True
    ticket.checked_in_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)

    _log_action(
        db, "Scanner", "checkin",
        f"Checked in {ticket.name} ({ticket.show_date}, {ticket.ticket_type} x{ticket.quantity})"
    )

    return schemas.CheckinResponse(
        success=True,
        message=f"Welcome, {ticket.name}!",
        ticket=ticket,
    )


# ---------------------------------------------------------------------------
# Backstage / Audit log  (admin only)
# ---------------------------------------------------------------------------

@app.get(
    "/api/admin/audit",
    dependencies=[Depends(verify_backstage)],
)
def get_audit_log(db: Session = Depends(get_db), limit: int = 200):
    """Return the most recent audit log entries (dashboard key required)."""
    entries = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return {
        "entries": [
            {
                "id":        e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "role":      e.role,
                "action":    e.action,
                "detail":    e.detail,
            }
            for e in entries
        ]
    }
