import hashlib, json, uuid
from decimal import Decimal
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import *
from app.schemas import *
from app.auth import current_user, require_roles
from app.settings import settings

app = FastAPI(title="Banking Core Operations API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
ROLES = ("customer", "teller", "operations", "compliance_officer", "auditor", "administrator")

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/health/db")
def db_health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1")); return {"status": "ok", "database": "connected"}

@app.get("/api/me")
def me(user: ApplicationUser = Depends(current_user)):
    return {"id": str(user.id), "email": user.email, "roles": [r.name for r in user.roles]}

@app.get("/api/dashboard")
def dashboard(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    role_names = {r.name for r in user.roles}
    if role_names.intersection({"administrator", "auditor", "operations", "teller", "compliance_officer"}):
        return {"customers": db.query(func.count(Customer.id)).scalar(), "accounts": db.query(func.count(Account.id)).scalar(), "transfers": db.query(func.count(Transfer.id)).scalar(), "pending_kyc": db.query(func.count(KYCRecord.id)).filter_by(status="pending").scalar(), "roles": sorted(role_names)}
    customer = db.query(Customer).filter_by(user_id=user.id).first()
    accounts = db.query(Account).filter_by(customer_id=customer.id).all() if customer else []
    return {"customers": 1 if customer else 0, "accounts": len(accounts), "transfers": 0, "pending_kyc": 0, "roles": sorted(role_names)}

@app.post("/api/customers", status_code=201)
def create_customer(data: CustomerCreate, user: ApplicationUser = Depends(require_roles("administrator", "teller", "operations", "customer")), db: Session = Depends(get_db)):
    is_customer = "customer" in {r.name for r in user.roles}
    if is_customer and db.query(Customer.id).filter_by(user_id=user.id).first(): raise HTTPException(409, "Customer profile already exists")
    customer = Customer(user_id=user.id if is_customer else None, full_name=data.full_name, email=data.email, status="pending")
    db.add(customer); db.flush()
    db.add(AuditLog(actor_id=user.id, action="customer.created", entity_type="customer", entity_id=str(customer.id)))
    db.commit(); db.refresh(customer)
    return {"id": str(customer.id), "name": customer.full_name, "status": customer.status}

@app.get("/api/customers")
def list_customers(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}
    q = db.query(Customer)
    if not roles.intersection({"administrator", "auditor", "operations", "teller", "compliance_officer"}): q = q.filter_by(user_id=user.id)
    return [{"id": str(x.id), "name": x.full_name, "email": x.email, "status": x.status} for x in q.order_by(Customer.created_at.desc()).limit(200)]

@app.post("/api/kyc/{customer_id}", status_code=201)
def submit_kyc(customer_id: uuid.UUID, data: KYCSubmit, user: ApplicationUser = Depends(require_roles("customer", "teller", "operations")), db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if not customer: raise HTTPException(404, "Customer not found")
    if "customer" in {r.name for r in user.roles} and customer.user_id != user.id: raise HTTPException(403, "Not your customer profile")
    row = KYCRecord(customer_id=customer_id, document_type=data.document_type, synthetic_reference=data.synthetic_reference, status="pending")
    db.add(row); db.add(AuditLog(actor_id=user.id, action="kyc.submitted", entity_type="kyc", entity_id=str(row.id))); db.commit(); db.refresh(row)
    return {"id": str(row.id), "status": row.status}

@app.get("/api/kyc")
def list_kyc(user: ApplicationUser = Depends(require_roles("compliance_officer", "administrator", "auditor", "operations")), db: Session = Depends(get_db)):
    return [{"id": str(k.id), "customer_id": str(k.customer_id), "status": k.status, "document_type": k.document_type, "reference": k.synthetic_reference} for k in db.query(KYCRecord).order_by(KYCRecord.submitted_at.desc()).limit(300)]

@app.post("/api/kyc/{kyc_id}/review")
def review_kyc(kyc_id: uuid.UUID, data: ReviewCreate, user: ApplicationUser = Depends(require_roles("compliance_officer", "administrator")), db: Session = Depends(get_db)):
    row = db.get(KYCRecord, kyc_id)
    if not row: raise HTTPException(404, "KYC record not found")
    if row.status != "pending": raise HTTPException(409, "KYC record already reviewed")
    row.status = data.decision
    db.add(KYCReview(kyc_id=row.id, reviewer_id=user.id, decision=data.decision, notes=data.notes))
    if data.decision == "approved": db.get(Customer, row.customer_id).status = "active"
    db.add(AuditLog(actor_id=user.id, action="kyc.reviewed", entity_type="kyc", entity_id=str(row.id), details={"decision": data.decision})); db.commit()
    return {"id": str(row.id), "status": row.status}

@app.post("/api/accounts", status_code=201)
def create_account(data: AccountCreate, user: ApplicationUser = Depends(require_roles("administrator", "teller", "operations")), db: Session = Depends(get_db)):
    customer = db.get(Customer, data.customer_id)
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "active": raise HTTPException(409, "Customer must pass KYC before account opening")
    account = Account(customer_id=customer.id, account_number="D" + uuid.uuid4().hex[:15].upper(), currency=data.currency.upper(), status="active", balance=Decimal("0.00"))
    db.add(account); db.flush(); db.add(AuditLog(actor_id=user.id, action="account.opened", entity_type="account", entity_id=str(account.id))); db.commit(); db.refresh(account)
    return {"id": str(account.id), "account_number": account.account_number, "currency": account.currency, "balance": str(account.balance)}

@app.get("/api/accounts")
def list_accounts(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}; q = db.query(Account)
    if not roles.intersection({"administrator", "auditor", "operations", "teller", "compliance_officer"}):
        q = q.join(Customer).filter(Customer.user_id == user.id)
    return [{"id": str(a.id), "account_number": a.account_number, "customer_id": str(a.customer_id), "currency": a.currency, "balance": str(a.balance), "status": a.status} for a in q.order_by(Account.created_at.desc()).limit(500)]

@app.post("/api/transfers", status_code=201)
def transfer(data: TransferCreate, idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"), user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}; payload = data.model_dump(mode="json"); digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    try:
        # Serialize requests sharing a key before inspecting the key table.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": idempotency_key})
        prior = db.get(IdempotencyKey, idempotency_key)
        if prior:
            if prior.request_hash != digest: raise HTTPException(409, "Idempotency key reused with different request")
            t = db.get(Transfer, prior.transfer_id)
            return {"id": str(t.id), "status": t.status, "amount": str(t.amount), "currency": t.currency}
        ids = sorted([data.source_account_id, data.destination_account_id], key=str)
        locked = db.execute(select(Account).where(Account.id.in_(ids)).order_by(Account.id).with_for_update()).scalars().all()
        accounts = {a.id: a for a in locked}
        src, dst = accounts.get(data.source_account_id), accounts.get(data.destination_account_id)
        if not src or not dst: raise HTTPException(404, "Account not found")
        if src.id == dst.id: raise HTTPException(422, "Source and destination must differ")
        if not roles.intersection({"administrator", "teller", "operations"}):
            own = db.query(Customer.id).filter_by(user_id=user.id).scalar()
            if not own or src.customer_id != own: raise HTTPException(403, "Source account is not yours")
        if src.status != "active" or dst.status != "active": raise HTTPException(409, "Both accounts must be active")
        if src.currency != dst.currency or data.currency != src.currency: raise HTTPException(422, "Currency mismatch")
        if src.balance < data.amount: raise HTTPException(409, "Insufficient funds")
        t = Transfer(source_account_id=src.id, destination_account_id=dst.id, amount=data.amount, currency=data.currency, status="posted", reference=data.reference)
        db.add(t); db.flush()
        tx = LedgerTransaction(transfer_id=t.id, description=data.reference or "Internal transfer", status="posted"); db.add(tx); db.flush()
        db.add_all([LedgerEntry(transaction_id=tx.id, account_id=src.id, amount=data.amount, currency=data.currency, direction="debit"), LedgerEntry(transaction_id=tx.id, account_id=dst.id, amount=data.amount, currency=data.currency, direction="credit")])
        src.balance -= data.amount; dst.balance += data.amount
        db.add(IdempotencyKey(key=idempotency_key, request_hash=digest, transfer_id=t.id))
        db.add(AuditLog(actor_id=user.id, action="transfer.posted", entity_type="transfer", entity_id=str(t.id), details={"amount": str(data.amount), "currency": data.currency}))
        db.commit()
        return {"id": str(t.id), "status": t.status, "amount": str(t.amount), "currency": t.currency}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise HTTPException(409, "Transfer could not be posted; retry with the same idempotency key") from None

@app.get("/api/transfers")
def list_transfers(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}; q = db.query(Transfer)
    if not roles.intersection({"administrator", "auditor", "operations", "teller", "compliance_officer"}):
        own = db.query(Customer.id).filter_by(user_id=user.id).scalar()
        if not own: return []
        ids = db.query(Account.id).filter_by(customer_id=own).subquery()
        q = q.filter((Transfer.source_account_id.in_(ids)) | (Transfer.destination_account_id.in_(ids)))
    return [{"id": str(t.id), "source_account_id": str(t.source_account_id), "destination_account_id": str(t.destination_account_id), "amount": str(t.amount), "currency": t.currency, "status": t.status, "reference": t.reference, "created_at": t.created_at.isoformat()} for t in q.order_by(Transfer.created_at.desc()).limit(300)]

@app.get("/api/accounts/{account_id}/statement")
def statement(account_id: uuid.UUID, user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account: raise HTTPException(404, "Account not found")
    roles = {r.name for r in user.roles}
    if not roles.intersection({"administrator", "auditor", "operations", "teller"}):
        c = db.query(Customer.id).filter_by(user_id=user.id).scalar()
        if account.customer_id != c: raise HTTPException(403, "Not your account")
    entries = db.query(LedgerEntry, LedgerTransaction).join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id).filter(LedgerEntry.account_id == account.id, LedgerTransaction.status == "posted").order_by(LedgerEntry.created_at.desc()).limit(500).all()
    return {"account_id": str(account.id), "currency": account.currency, "balance": str(account.balance), "transactions": [{"id": str(e.id), "transaction_id": str(tx.id), "amount": str(e.amount), "direction": e.direction, "created_at": e.created_at.isoformat(), "description": tx.description} for e,tx in entries]}

@app.get("/api/reports/reconciliation")
def reconciliation(user: ApplicationUser = Depends(require_roles("auditor", "administrator", "operations")), db: Session = Depends(get_db)):
    rows = db.query(LedgerEntry.currency, LedgerEntry.direction, func.sum(LedgerEntry.amount)).join(LedgerTransaction).filter(LedgerTransaction.status == "posted").group_by(LedgerEntry.currency, LedgerEntry.direction).all()
    result = {}
    numeric = {}
    for currency, direction, total in rows:
        numeric.setdefault(currency, {})[direction] = total or Decimal("0")
        result.setdefault(currency, {})[direction] = str(total or 0)
    return {"balanced": all(v.get("debit", Decimal("0")) == v.get("credit", Decimal("0")) for v in numeric.values()), "totals": result}

@app.get("/api/audit")
def audit(user: ApplicationUser = Depends(require_roles("auditor", "administrator")), db: Session = Depends(get_db)):
    return [{"id": str(a.id), "actor_id": str(a.actor_id) if a.actor_id else None, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "details": a.details, "created_at": a.created_at.isoformat()} for a in db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)]

@app.get("/api/admin/roles")
def roles(user: ApplicationUser = Depends(require_roles("administrator")), db: Session = Depends(get_db)):
    return [{"id": u.id, "email": u.email, "supabase_sub": u.supabase_sub, "roles": [r.name for r in u.roles]} for u in db.query(ApplicationUser).order_by(ApplicationUser.created_at.desc()).limit(500)]

@app.put("/api/admin/roles/{user_id}")
def set_roles(user_id: uuid.UUID, role_names: list[str], user: ApplicationUser = Depends(require_roles("administrator")), db: Session = Depends(get_db)):
    if any(name not in ROLES for name in role_names): raise HTTPException(422, "Unknown role")
    target = db.get(ApplicationUser, user_id)
    if not target: raise HTTPException(404, "User not found")
    roles_by_name = {r.name:r for r in db.query(Role).all()}
    for name in ROLES: roles_by_name.setdefault(name, Role(name=name))
    target.roles = [roles_by_name[name] for name in role_names]
    db.add(AuditLog(actor_id=user.id, action="user.roles_changed", entity_type="user", entity_id=str(user_id), details={"roles": role_names})); db.commit()
    return {"id": str(target.id), "roles": [r.name for r in target.roles]}

@app.get("/api/admin/permissions")
def permissions(user: ApplicationUser = Depends(current_user)):
    return {"roles": list(ROLES), "permissions": {"customer": ["own profile/accounts/statements/transfers"], "teller": ["read customers", "open accounts", "initiate transfers"], "operations": ["teller permissions", "review operations"], "compliance_officer": ["review KYC", "view compliance queue"], "auditor": ["read reports", "reconciliation", "audit trail"], "administrator": ["manage users and roles", "all operations"]}}
