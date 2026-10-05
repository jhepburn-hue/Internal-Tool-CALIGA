import os
from flask import Flask
from models import db, User
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
db_url = os.getenv('DATABASE_URL')
if not db_url:
    raise ValueError("DATABASE_URL is not set in the .env file!")

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def initialize_database():
    with app.app_context():
        print("Creating all database tables on the remote PostgreSQL instance...")
        db.create_all()
        print("Database tables created successfully!")

        if User.query.count() == 0:
            team_users = [
                User(first_name="James", last_name_initial="H", email="jhepburn@wavelynx.com", role="SET"),
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
    initialize_database()