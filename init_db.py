"""
Seeds the Wavelynx team users. Create or update the tables first with:

    flask --app app db upgrade
"""
from app import app
from models import db, User


def seed_users():
    with app.app_context():
        if User.query.count() == 0:
            team_users = [
                User(first_name="James", last_name_initial="H", email="jhepburn@wavelynx.com", role="SET", is_admin=True),
                User(first_name="Dennis", last_name_initial="P", email="dpsimaris@wavelynx.com", role="SET"),
                User(first_name="Shelby", last_name_initial="O", email="soconnell@wavelynx.com", role="SET"),
                User(first_name="Jacob", last_name_initial="M", email="jacobm@wavelynx.com", role="SET"),

                User(first_name="Rebecca", last_name_initial="R", email="rramirez@wavelynx.com", role="QA"),
                User(first_name="Jake", last_name_initial="S", email="jstrande@wavelynx.com", role="QA"),

                User(first_name="Anthony", last_name_initial="K", email="akowalik@wavelynx.com", role="Sales"),
                User(first_name="Nate", last_name_initial="D", email="nate@wavelynx.com", role="Sales")
            ]
            db.session.add_all(team_users)
            db.session.commit()
            print("Wavelynx team members seeded successfully!")
        else:
            print("Users already exist in the database.")


if __name__ == '__main__':
    seed_users()
