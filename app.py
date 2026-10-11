import os
import logging
from dotenv import load_dotenv

# Load .env before importing services. They read their settings at import time.
load_dotenv()

from flask import Flask, request, redirect, url_for, flash, jsonify
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.exceptions import HTTPException
from models import db, User
from auth import get_current_user
from services.gcs_client import check_gcs_credentials
from routes.helpers import wants_json
from routes import main, configurations, testing, failures, tools, jobs

csrf = CSRFProtect()
migrate = Migrate()

# Pages that work without picking a user first.
PUBLIC_ENDPOINTS = {'main.home', 'main.switch_user', 'static'}


def create_app():
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )

    secret_key = os.getenv('SECRET_KEY')
    db_url = os.getenv('DATABASE_URL')
    if not secret_key:
        raise RuntimeError("SECRET_KEY is not set. Add it to .env (see README).")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env (see README).")
    check_gcs_credentials()

    app = Flask(__name__)
    app.config['SECRET_KEY'] = secret_key
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    engine_options = {
        "pool_pre_ping": True,
        "pool_recycle": 300
    }
    if db_url.startswith("postgresql+psycopg"):
        # Supabase's transaction pooler (port 6543) shares server connections between
        # clients, so psycopg's automatic prepared statements collide
        # ("prepared statement _pg3_N already exists"). Disable them.
        engine_options["connect_args"] = {"prepare_threshold": None}
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Builds and flashes can outlive a normal CSRF token window.
    app.config['WTF_CSRF_TIME_LIMIT'] = None

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    for module in (main, configurations, testing, failures, tools, jobs):
        app.register_blueprint(module.bp)

    @app.context_processor
    def inject_user():
        return dict(
            current_user=get_current_user(),
            all_users=User.query.order_by(User.role, User.first_name).all()
        )

    @app.before_request
    def require_user():
        if request.endpoint in PUBLIC_ENDPOINTS or get_current_user() is not None:
            return None
        if wants_json():
            return jsonify({"error": "Pick a user from the menu first."}), 401
        flash("Pick a user from the menu to continue.", "warning")
        return redirect(url_for('main.home'))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        if wants_json():
            return jsonify({"error": "Your form expired. Reload the page and try again."}), 400
        flash("Your form expired. Please try again.", "warning")
        return redirect(request.referrer or url_for('main.home'))

    @app.errorhandler(403)
    def handle_forbidden(e: HTTPException):
        message = e.description or "You don't have permission to do that."
        if wants_json():
            return jsonify({"error": message}), 403
        flash(message, "danger")
        # GET pages go home to avoid redirect loops. Form posts go back to the page they came from.
        if request.method == 'GET':
            return redirect(url_for('main.home'))
        return redirect(request.referrer or url_for('main.home'))

    @app.errorhandler(404)
    def handle_not_found(e: HTTPException):
        if wants_json():
            return jsonify({"error": e.description}), 404
        return e

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', '0') == '1', port=5000)
