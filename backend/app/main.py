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
MANAGER_ROLES = {"administrator", "operations", "teller", "compliance_officer", "auditor"}

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
    if role_names.intersection(MANAGER_ROLES):
        today = func.date(Transfer.created_at) == func.current_date()
        totals = reconciliation(user, db)
        recent = db.query(Transfer).filter_by(status="posted").order_by(Transfer.created_at.desc()).limit(5).all()
        return {"view":"manager", "customers": db.query(func.count(Customer.id)).scalar(), "accounts": db.query(func.count(Account.id)).filter_by(status="active").scalar(), "transfers": db.query(func.count(Transfer.id)).filter(today, Transfer.status=="posted").scalar(), "pending_kyc": db.query(func.count(KYCRecord.id)).filter_by(status="pending").scalar(), "ledger": totals, "recent_transfers":[{"id":str(t.id),"amount":str(t.amount),"currency":t.currency,"reference":t.reference,"created_at":t.created_at.isoformat()} for t in recent], "roles": sorted(role_names)}
    customer = db.query(Customer).filter_by(user_id=user.id).first()
    accounts = db.query(Account).filter_by(customer_id=customer.id).all() if customer else []
    transfers_count = db.query(func.count(Transfer.id)).filter((Transfer.source_account_id.in_([a.id for a in accounts])) | (Transfer.destination_account_id.in_([a.id for a in accounts]))).scalar() if accounts else 0
    kyc = db.query(KYCRecord).filter_by(customer_id=customer.id).order_by(KYCRecord.submitted_at.desc()).first() if customer else None
    return {"view": "customer", "customers": 1 if customer else 0, "accounts": len(accounts), "transfers": transfers_count, "pending_kyc": int(kyc is not None and kyc.status == "pending"), "customer_status": customer.status if customer else "profile_required", "kyc_status": kyc.status if kyc else "not_submitted", "roles": sorted(role_names)}

@app.post("/api/customers", status_code=201)
def create_customer(data: CustomerCreate, user: ApplicationUser = Depends(require_roles("administrator", "teller", "operations", "customer")), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}
    is_customer = "customer" in roles and not roles.intersection(MANAGER_ROLES)
    if is_customer and db.query(Customer.id).filter_by(user_id=user.id).first(): raise HTTPException(409, "Customer profile already exists")
    if not is_customer and db.query(Customer.id).filter_by(email=data.email).first(): raise HTTPException(409, "A customer profile already exists for this email")
    customer = Customer(user_id=user.id if is_customer else None, full_name=data.full_name, email=(user.email or data.email) if is_customer else data.email, status="pending")
    db.add(customer); db.flush()
    db.add(AuditLog(actor_id=user.id, action="customer.created", entity_type="customer", entity_id=str(customer.id)))
    db.commit(); db.refresh(customer)
    return {"id": str(customer.id), "name": customer.full_name, "email": customer.email, "status": customer.status}

@app.get("/api/customers")
def list_customers(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}
    q = db.query(Customer)
    if not roles.intersection(MANAGER_ROLES): q = q.filter_by(user_id=user.id)
    rows=[]
    for x in q.order_by(Customer.created_at.desc()).limit(200):
        kyc=db.query(KYCRecord).filter_by(customer_id=x.id).order_by(KYCRecord.submitted_at.desc()).first()
        rows.append({"id":str(x.id),"name":x.full_name,"email":x.email,"status":x.status,"kyc_status":kyc.status if kyc else "not_submitted","profile_type":"linked user" if x.user_id else "bank-created profile"})
    return rows

@app.post("/api/kyc/{customer_id}", status_code=201)
def submit_kyc(customer_id: uuid.UUID, data: KYCSubmit, user: ApplicationUser = Depends(require_roles("customer", "teller", "operations", "administrator")), db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if not customer: raise HTTPException(404, "Customer not found")
    roles = {r.name for r in user.roles}
    if not roles.intersection(MANAGER_ROLES) and customer.user_id != user.id: raise HTTPException(403, "Not your customer profile")
    if db.query(KYCRecord.id).filter_by(customer_id=customer_id, status="pending").first(): raise HTTPException(409, "A KYC submission is already awaiting review")
    row = KYCRecord(customer_id=customer_id, document_type=data.document_type, synthetic_reference=data.synthetic_reference, status="pending")
    db.add(row); db.add(AuditLog(actor_id=user.id, action="kyc.submitted", entity_type="kyc", entity_id=str(row.id))); db.commit(); db.refresh(row)
    return {"id": str(row.id), "status": row.status, "reason": None}

@app.get("/api/kyc/mine")
def my_kyc(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    customer = db.query(Customer).filter_by(user_id=user.id).first()
    if not customer: return {"customer_id": None, "customer_status": "profile_required", "submissions": []}
    rows = db.query(KYCRecord).filter_by(customer_id=customer.id).order_by(KYCRecord.submitted_at.desc()).all()
    reviews = {r.kyc_id: r for r in db.query(KYCReview).filter(KYCReview.kyc_id.in_([x.id for x in rows])).all()} if rows else {}
    return {"customer_id": str(customer.id), "customer_status": customer.status, "submissions": [{"id": str(x.id), "status": x.status, "document_type": x.document_type, "synthetic_reference": x.synthetic_reference, "reason": reviews[x.id].notes if x.id in reviews else None} for x in rows]}

@app.get("/api/kyc/{customer_id}/mine")
def customer_kyc(customer_id: uuid.UUID, user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if not customer: raise HTTPException(404, "Customer not found")
    roles = {r.name for r in user.roles}
    if not roles.intersection(MANAGER_ROLES) and customer.user_id != user.id: raise HTTPException(403, "Not your customer profile")
    rows = db.query(KYCRecord).filter_by(customer_id=customer.id).order_by(KYCRecord.submitted_at.desc()).all()
    reviews = {r.kyc_id: r for r in db.query(KYCReview).filter(KYCReview.kyc_id.in_([x.id for x in rows])).all()} if rows else {}
    return [{"id": str(x.id), "status": x.status, "document_type": x.document_type, "synthetic_reference": x.synthetic_reference, "reason": reviews[x.id].notes if x.id in reviews else None} for x in rows]

@app.get("/api/kyc")
def list_kyc(status: str | None = None, user: ApplicationUser = Depends(require_roles("compliance_officer", "administrator", "auditor", "operations")), db: Session = Depends(get_db)):
    if status and status not in {"pending","approved","rejected"}: raise HTTPException(422,"Invalid KYC status filter")
    query=db.query(KYCRecord)
    if status: query=query.filter_by(status=status)
    return [{"id":str(k.id),"customer_id":str(k.customer_id),"status":k.status,"document_type":k.document_type,"reference":k.synthetic_reference,"submitted_at":k.submitted_at.isoformat(),"customer_name":db.get(Customer,k.customer_id).full_name} for k in query.order_by(KYCRecord.submitted_at.desc()).limit(300)]

@app.post("/api/kyc/{kyc_id}/review")
def review_kyc(kyc_id: uuid.UUID, data: ReviewCreate, user: ApplicationUser = Depends(require_roles("compliance_officer", "administrator")), db: Session = Depends(get_db)):
    row = db.execute(select(KYCRecord).where(KYCRecord.id==kyc_id).with_for_update()).scalar_one_or_none()
    if not row: raise HTTPException(404, "KYC record not found")
    if row.status != "pending": raise HTTPException(409, "KYC record already reviewed")
    row.status = data.decision
    db.add(KYCReview(kyc_id=row.id, reviewer_id=user.id, decision=data.decision, notes=data.notes))
    if data.decision == "approved": db.get(Customer, row.customer_id).status = "active"
    db.add(AuditLog(actor_id=user.id, action="kyc.reviewed", entity_type="kyc", entity_id=str(row.id), details={"decision": data.decision, "reason": data.notes})); db.commit()
    return {"id": str(row.id), "status": row.status, "reason": data.notes}

@app.post("/api/accounts", status_code=201)
def create_account(data: AccountCreate, user: ApplicationUser = Depends(require_roles("administrator", "teller", "operations")), db: Session = Depends(get_db)):
    customer = db.get(Customer, data.customer_id)
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "active" or not db.query(KYCRecord.id).filter_by(customer_id=customer.id, status="approved").first(): raise HTTPException(409, "Customer must pass KYC approval before account opening")
    account = Account(customer_id=customer.id, account_number="D" + uuid.uuid4().hex[:15].upper(), account_type=data.account_type, currency=data.currency.upper(), status="active", balance=Decimal("0.00"))
    db.add(account); db.flush(); db.add(AuditLog(actor_id=user.id, action="account.opened", entity_type="account", entity_id=str(account.id))); db.commit(); db.refresh(account)
    return {"id": str(account.id), "account_number": account.account_number, "account_type": account.account_type, "customer_name": customer.full_name, "currency": account.currency, "balance": str(account.balance), "status": account.status}

@app.post("/api/accounts/{account_id}/fund")
def fund_account(account_id: uuid.UUID, data: FundingCreate, idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"), user: ApplicationUser = Depends(require_roles("administrator")), db: Session = Depends(get_db)):
    amount=data.amount
    target=db.get(Account,account_id)
    if not target: raise HTTPException(404,"Account not found")
    if target.account_type=="system_clearing": raise HTTPException(422,"System clearing accounts cannot be funded")
    clearing=db.query(Account).filter_by(account_type="system_clearing",currency=target.currency,status="active").first()
    if not clearing: raise HTTPException(409,"No active system clearing account; seed demo records first")
    payload=TransferCreate(source_account_id=clearing.id,destination_account_id=target.id,amount=amount,currency=target.currency,reference="DEMO-FUNDING")
    result=transfer(payload,idempotency_key,user,db)
    prior_funding=any(a.details.get("transfer_id")==result["id"] for a in db.query(AuditLog).filter_by(action="account.funded",entity_id=str(target.id)).all())
    if not prior_funding:
        db.add(AuditLog(actor_id=user.id,action="account.funded",entity_type="account",entity_id=str(target.id),details={"transfer_id":result["id"],"amount":str(amount),"currency":target.currency,"source":"system_clearing"})); db.commit()
    return result

@app.get("/api/accounts")
def list_accounts(user: ApplicationUser = Depends(current_user), db: Session = Depends(get_db)):
    roles = {r.name for r in user.roles}; q = db.query(Account)
    if not roles.intersection(MANAGER_ROLES):
        q = q.join(Customer).filter(Customer.user_id == user.id)
    return [{"id": str(a.id), "account_number": a.account_number, "customer_id": str(a.customer_id), "customer_name": db.get(Customer,a.customer_id).full_name, "account_type": a.account_type, "currency": a.currency, "balance": str(a.balance), "status": a.status} for a in q.order_by(Account.created_at.desc()).limit(500)]

@app.patch("/api/accounts/{account_id}")
def maintain_account(account_id: uuid.UUID, data: AccountStatusUpdate, user: ApplicationUser = Depends(require_roles("administrator", "operations")), db: Session = Depends(get_db)):
    account = db.execute(select(Account).where(Account.id==account_id).with_for_update()).scalar_one_or_none()
    if not account: raise HTTPException(404, "Account not found")
    if account.status == "closed": raise HTTPException(409, "Closed accounts cannot be changed")
    if data.status == "closed" and account.balance != Decimal("0.00"): raise HTTPException(409, "Account must have a zero balance before closure")
    previous = account.status
    account.status = data.status
    db.add(AuditLog(actor_id=user.id, action="account.status_changed", entity_type="account", entity_id=str(account.id), details={"from": previous, "to": data.status}))
    db.commit()
    return {"id": str(account.id), "status": account.status}

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
    if not roles.intersection(MANAGER_ROLES):
        own = db.query(Customer.id).filter_by(user_id=user.id).scalar()
        if not own: return []
        ids = db.query(Account.id).filter_by(customer_id=own).subquery()
        q = q.filter((Transfer.source_account_id.in_(ids)) | (Transfer.destination_account_id.in_(ids)))
    rows=[]
    for t in q.order_by(Transfer.created_at.desc()).limit(300):
        src=db.get(Account,t.source_account_id); dst=db.get(Account,t.destination_account_id)
        ledger=db.query(LedgerTransaction).filter_by(transfer_id=t.id).first()
        rows.append({"id":str(t.id),"transaction_id":str(ledger.id) if ledger else None,"source_account_id":str(t.source_account_id),"source_account_number":src.account_number,"destination_account_id":str(t.destination_account_id),"destination_account_number":dst.account_number,"amount":str(t.amount),"currency":t.currency,"status":t.status,"reference":t.reference,"created_at":t.created_at.isoformat()})
    return rows

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
    mismatches={c:{"debit":str(v.get("debit",Decimal("0"))),"credit":str(v.get("credit",Decimal("0")))} for c,v in numeric.items() if v.get("debit",Decimal("0")) != v.get("credit",Decimal("0"))}
    posted=db.query(LedgerEntry.account_id,LedgerEntry.direction,func.sum(LedgerEntry.amount)).join(LedgerTransaction).filter(LedgerTransaction.status=="posted").group_by(LedgerEntry.account_id,LedgerEntry.direction).all()
    by_account={}
    for aid,direction,total in posted: by_account.setdefault(aid,{"debit":Decimal("0"),"credit":Decimal("0")})[direction]=total or Decimal("0")
    account_mismatches=[]
    for account in db.query(Account).all():
        sums=by_account.get(account.id,{"debit":Decimal("0"),"credit":Decimal("0")})
        expected=sums["debit"]-sums["credit"] if account.account_type=="system_clearing" else sums["credit"]-sums["debit"]
        if expected!=account.balance: account_mismatches.append({"account_id":str(account.id),"account_number":account.account_number,"projection":str(account.balance),"ledger_balance":str(expected)})
    return {"balanced": not mismatches and not account_mismatches, "totals": result,"discrepancies":mismatches,"account_discrepancies":account_mismatches,"posted_entries":db.query(func.count(LedgerEntry.id)).join(LedgerTransaction).filter(LedgerTransaction.status=="posted").scalar()}

@app.get("/api/audit")
def audit(user: ApplicationUser = Depends(require_roles("auditor", "administrator")), db: Session = Depends(get_db)):
    rows=[]
    for a in db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(500):
        actor=db.get(ApplicationUser,a.actor_id) if a.actor_id else None
        rows.append({"id":str(a.id),"actor_id":str(a.actor_id) if a.actor_id else None,"actor_email":actor.email if actor else None,"action":a.action,"entity_type":a.entity_type,"entity_id":a.entity_id,"details":a.details,"created_at":a.created_at.isoformat()})
    return rows

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
