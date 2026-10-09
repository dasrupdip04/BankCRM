"""Explicit administrative commands; never invoked automatically during login."""
import argparse
import uuid
from decimal import Decimal
from sqlalchemy import select
from app.db import SessionLocal
from app.models import ApplicationUser, Role, AuditLog, Customer, KYCRecord, KYCReview, Account, LedgerTransaction, LedgerEntry
from app.schemas import TransferCreate
from app.main import transfer

MANAGER_EMAIL = "dasrupdip04@gmail.com"

def provision_manager(email: str = MANAGER_EMAIL):
    if email.lower() != MANAGER_EMAIL:
        raise SystemExit(f"Refusing to provision {email}; expected {MANAGER_EMAIL}")
    with SessionLocal.begin() as db:
        user = db.execute(select(ApplicationUser).where(ApplicationUser.email.ilike(MANAGER_EMAIL)).with_for_update()).scalar_one_or_none()
        if user is None:
            raise SystemExit("No application-user record for this email. Sign in with Google first, then run this command again.")
        if (user.email or "").lower() != MANAGER_EMAIL:
            raise SystemExit("Authenticated application-user email does not match the expected manager email")
        role = db.execute(select(Role).where(Role.name == "administrator")).scalar_one_or_none()
        if role is None:
            role = Role(name="administrator"); db.add(role); db.flush()
        if role not in user.roles:
            user.roles.append(role)
            db.add(AuditLog(actor_id=user.id, action="user.manager_provisioned", entity_type="user", entity_id=str(user.id), details={"email": MANAGER_EMAIL, "method": "explicit_cli"}))
            print(f"Manager role assigned to verified application user {user.id} ({MANAGER_EMAIL}).")
        else:
            print(f"{MANAGER_EMAIL} already has the manager role; no changes made.")

def seed_demo():
    """Create additive, deterministic synthetic records through balanced ledger flows."""
    with SessionLocal() as db:
        manager = db.execute(select(ApplicationUser).where(ApplicationUser.email.ilike(MANAGER_EMAIL))).scalar_one_or_none()
        if manager is None or "administrator" not in {r.name for r in manager.roles}:
            raise SystemExit("Provision and sign in the manager before seeding demo records")
        profiles = [
            ("demo-alice", "Alice Demo", "demo.alice@example.test", "approved"),
            ("demo-bob", "Bob Demo", "demo.bob@example.test", "approved"),
            ("demo-pending", "Pat Pending", "demo.pending@example.test", "pending"),
            ("demo-rejected", "Robin Rejected", "demo.rejected@example.test", "rejected"),
            ("demo-clearing", "Northstar Demo Clearing", "demo.clearing@example.test", "approved"),
        ]
        people = {}
        for slug, name, email, status in profiles:
            customer = db.query(Customer).filter_by(email=email).first()
            if not customer:
                customer = Customer(full_name=name, email=email, status="active" if status=="approved" else "pending")
                db.add(customer); db.flush()
                db.add(AuditLog(actor_id=manager.id, action="demo.customer_seeded", entity_type="customer", entity_id=str(customer.id), details={"synthetic":True,"case":slug}))
            kyc = db.query(KYCRecord).filter_by(customer_id=customer.id, synthetic_reference=f"DEMO-{slug.upper()}").first()
            if not kyc:
                kyc = KYCRecord(customer_id=customer.id, document_type="synthetic-demo", synthetic_reference=f"DEMO-{slug.upper()}", status=status)
                db.add(kyc); db.flush()
                if status != "pending":
                    db.add(KYCReview(kyc_id=kyc.id, reviewer_id=manager.id, decision=status, notes="Synthetic demo decision"))
                    db.add(AuditLog(actor_id=manager.id, action="demo.kyc_decided", entity_type="kyc", entity_id=str(kyc.id), details={"decision":status,"synthetic":True}))
            people[slug]=customer
        db.commit()
        accounts={}
        for slug in ("demo-alice","demo-bob","demo-clearing"):
            customer=people[slug]
            account_type="system_clearing" if slug=="demo-clearing" else "checking"
            account=db.query(Account).filter_by(customer_id=customer.id, account_type=account_type, currency="USD").first()
            if not account:
                account=Account(customer_id=customer.id,account_number="D"+uuid.uuid5(uuid.NAMESPACE_DNS,slug).hex[:15].upper(),account_type=account_type,currency="USD",status="active",balance=Decimal("0.00"))
                db.add(account); db.flush()
                db.add(AuditLog(actor_id=manager.id,action="demo.account_opened",entity_type="account",entity_id=str(account.id),details={"synthetic":True}))
            accounts[slug]=account
        db.commit()
        # System clearing is the balancing counter-account; every funding event has equal debit and credit.
        for slug, amount in (("demo-alice",Decimal("2500.00")),("demo-bob",Decimal("1800.00"))):
            account=accounts[slug]
            marker=f"demo-opening-{slug}"
            exists=db.query(LedgerTransaction).filter_by(description=marker).first()
            if not exists:
                tx=LedgerTransaction(description=marker,status="posted"); db.add(tx); db.flush()
                db.add_all([LedgerEntry(transaction_id=tx.id,account_id=account.id,amount=amount,currency="USD",direction="credit"),LedgerEntry(transaction_id=tx.id,account_id=accounts["demo-clearing"].id,amount=amount,currency="USD",direction="debit")])
                account.balance+=amount; accounts["demo-clearing"].balance+=amount
                db.add(AuditLog(actor_id=manager.id,action="demo.funded",entity_type="account",entity_id=str(account.id),details={"amount":str(amount),"currency":"USD","contra_account":accounts["demo-clearing"].account_number}))
        db.commit()
        for i,(src,dst,amount) in enumerate((("demo-alice","demo-bob","125.00"),("demo-bob","demo-alice","42.50"),("demo-alice","demo-bob","18.25")),1):
            transfer(TransferCreate(source_account_id=accounts[src].id,destination_account_id=accounts[dst].id,amount=Decimal(amount),currency="USD",reference=f"DEMO-TRANSFER-{i}"),f"demo-seed-transfer-{i:02d}",manager,db)
        print("Synthetic demo records are ready; rerunning this command is safe.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["provision-manager", "seed-demo"])
    args = parser.parse_args()
    provision_manager() if args.command == "provision-manager" else seed_demo()
