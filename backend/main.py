import os
import secrets
import logging
import io
import smtplib
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

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

DASHBOARD_KEY    = os.environ.get("DASHBOARD_KEY", "admin321")
FINANCE_KEY      = os.environ.get("FINANCE_KEY",   "finance123")
SCANNER_KEY      = os.environ.get("SCANNER_KEY",   "admin123")
_raw_origins     = os.environ.get("CORS_ORIGINS", "*")
CORS_ORIGINS     = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

# ── SMTP email config ─────────────────────────────────────────────────────────
# Set these in your .env to enable confirmation emails.
# Leave SMTP_USER / SMTP_PASS blank to disable email (registration still works).
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")   # defaults to SMTP_USER if blank

# ── Default event settings ────────────────────────────────────────────────────
# These are used when no override exists in the database.
SETTING_DEFAULTS = {
    "event_name":         "Immersive Theatre Experience",
    "event_subtitle":     "Reserve Your Place",
    "event_description":  "Complete the form, pay, and upload your receipt to confirm your booking.",
    "show_dates":         "2026-04-19,2026-04-26",   # comma-separated ISO dates
    "early_bird_price":   "25",
    "standard_price":     "30",
    "early_bird_limit":   "30",
    "total_capacity":     "100",
    "duitnow_name":       "YOUR NAME / ORGANISATION",
    "duitnow_id":         "01X-XXX XXXX",
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
    """Convert '2026-04-19' → 'Sunday, 19 April 2026'."""
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%A, %-d %B %Y")
    except Exception:
        return iso_date


def _build_email_html(ticket, settings: dict, has_qr: bool) -> str:
    """Build a beautiful HTML confirmation email."""
    event_name  = settings.get("event_name", "Theatre Event")
    show_date   = _format_date(ticket.show_date)
    qty_label   = f'{ticket.quantity} ticket{"s" if ticket.quantity > 1 else ""}'

    # Use CID reference — email clients render this as an inline image.
    # data: URIs are blocked by Gmail/Outlook for security reasons.
    qr_img_html = (
        '<img src="cid:qrcode" alt="Entry QR Code" '
        'width="200" height="200" style="display:block;margin:0 auto;" />'
        if has_qr
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
    Send a booking confirmation email with embedded QR code.
    Non-blocking: any SMTP error is logged but NOT re-raised,
    so registration always succeeds even if email fails.
    """
    if not SMTP_USER or not SMTP_PASS:
        logger.info(
            "Email not configured (SMTP_USER/SMTP_PASS not set) — "
            "skipping confirmation email for %s.", ticket.ticket_id
        )
        return

    try:
        qr_png     = _generate_qr_png_bytes(ticket.ticket_id)
        html_body  = _build_email_html(ticket, settings, bool(qr_png))
        event_name = settings.get("event_name", "Theatre Event")

        # multipart/related lets the HTML reference the inline QR image via cid:
        msg = MIMEMultipart("related")
        msg["Subject"] = f"🎭 Booking Confirmed — {event_name}"
        msg["From"]    = SMTP_FROM or SMTP_USER
        msg["To"]      = ticket.email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        # Attach QR code as inline image — Content-ID matches cid:qrcode in HTML
        if qr_png:
            qr_part = MIMEImage(qr_png, _subtype="png")
            qr_part.add_header("Content-ID", "<qrcode>")
            qr_part.add_header("Content-Disposition", "inline", filename="ticket-qr.png")
            msg.attach(qr_part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(msg["From"], [ticket.email], msg.as_string())

        logger.info(
            "Confirmation email sent → %s (ticket %s)",
            ticket.email, ticket.ticket_id
        )

    except Exception as exc:
        logger.error(
            "Failed to send confirmation email to %s: %s",
            ticket.email, exc
        )


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


@app.get("/api/admin/ping")
def admin_ping(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    """Key verification — returns role so the frontend knows what access level was granted."""
    if x_admin_key == DASHBOARD_KEY:
        return {"ok": True, "role": "dashboard"}
    if x_admin_key == FINANCE_KEY:
        return {"ok": True, "role": "finance"}
    if x_admin_key == SCANNER_KEY:
        return {"ok": True, "role": "finance"}   # scanner key gets read-only dashboard access
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
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        row = db.query(models.Setting).filter(models.Setting.key == key).first()
        if row:
            row.value = str(value)
        else:
            db.add(models.Setting(key=key, value=str(value)))
    db.commit()
    return get_all_settings(db)


def _early_bird_sold(db: Session, show_date: str) -> int:
    """Return total Early Bird tickets sold (sum of quantities) for a show date."""
    result = (
        db.query(func.sum(models.Ticket.quantity))
        .filter(
            models.Ticket.show_date == show_date,
            models.Ticket.ticket_type == "Early Bird",
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
    settings        = get_all_settings(db)
    show_dates      = [d.strip() for d in settings["show_dates"].split(",") if d.strip()]
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

    # Enforce Early Bird capacity (uses quantity sum, not row count)
    if ticket.ticket_type == "Early Bird":
        eb_limit  = int(settings["early_bird_limit"])
        eb_sold   = _early_bird_sold(db, ticket.show_date)
        eb_remaining = max(0, eb_limit - eb_sold)
        if ticket.quantity > eb_remaining:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Only {eb_remaining} Early Bird ticket(s) remaining for this date. "
                    f"Please reduce your quantity or choose Standard."
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

    if update.name is not None:
        ticket.name = update.name.strip()
    if update.email is not None:
        ticket.email = update.email.strip().lower()
    if update.phone is not None:
        ticket.phone = update.phone.strip()
    if update.ticket_type is not None:
        ticket.ticket_type = update.ticket_type
    if update.show_date is not None:
        ticket.show_date = update.show_date
    if update.quantity is not None:
        ticket.quantity = update.quantity
    if update.payment_status is not None:
        ticket.payment_status = update.payment_status

    db.commit()
    db.refresh(ticket)
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
    db.delete(ticket)
    db.commit()
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

    return schemas.CheckinResponse(
        success=True,
        message=f"Welcome, {ticket.name}!",
        ticket=ticket,
    )
