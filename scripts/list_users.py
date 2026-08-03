from app.db.session import SessionLocal
from app.models.entities import User
from sqlalchemy import select

def run():
    with SessionLocal() as db:
        users=db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.display_name)).all()
        if not users:
            print("No users found. Run python -m scripts.seed first.")
            return
        print("TasteGraph users:")
        for user in users: print(f"{user.display_name}: {user.id}")

if __name__=="__main__": run()
