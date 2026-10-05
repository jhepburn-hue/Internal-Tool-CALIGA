import os
from flask import Flask, render_template, session, request, redirect, url_for
from models import db, User, Configuration, RevisionHistory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'caliga-dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.context_processor
def inject_user():
    user_id = session.get('user_id')
    current_user = None
    if user_id:
        current_user = User.query.get(user_id)
    if not current_user:
        current_user = User.query.filter_by(email="jhepburn@wavelynx.com").first() or User.query.first()
    return dict(current_user=current_user)

@app.route('/')
def home():
    role_descriptions = {
        'SET': [
            'Initialize and manage Firmware execution runs',
            'Modify parameters across reader configurations',
            'Execute test groups and validate individual criteria',
            'Review revision history logs and modification tracking',
            'Monitor Firmware run metrics and aggregate analytics',
            'Inspect issue details within the Failures repository',
            'Dispatch automated test requests to QA'
        ],
        'QA': [
            'Initiate firmware execution test cycles',
            'Execute assigned test suites and record validation criteria',
            'Analyze firmware test run telemetry and aggregate statistics'
        ],
        'Sales': [
            'Provision new reader configuration profiles',
            'Modify parameter details for draft configurations',
            'Toggle configuration deployment state between POC and Active',
            'Dispatch testing notification alerts to QA',
            'Review Firmware execution analytics and run statistics'
        ]
    }

    status_descriptions = {
        'Production': 'Validated configuration profile approved for official deployment and manufacturing.',
        'Active': 'Deployed profile requiring verification and testing prior to Production promotion.',
        'POC': 'Proof of Concept profile open for editing and preliminary experimentation.',
        'Archived': 'Deprecated configuration profile retained strictly for historical reference.'
    }

    return render_template(
        'home.html',
        role_descriptions=role_descriptions,
        status_descriptions=status_descriptions
    )

@app.route('/configurations')
def configurations_list():
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = Configuration.query

    if search_query:
        query = query.filter(
            (Configuration.config_name.ilike(f'%{search_query}%')) |
            (Configuration.config_id_hex.ilike(f'%{search_query}%')) |
            (Configuration.config_id_dec.ilike(f'%{search_query}%'))
        )

    if status_filter:
        query = query.filter(Configuration.status == status_filter)

    configurations = query.order_by(
        db.cast(db.cast(db.func.nullif(Configuration.config_id_dec, ''), db.Numeric), db.Integer).asc().nulls_last()
    ).all()

    return render_template(
        'configurations.html',
        configurations=configurations,
        search_query=search_query,
        status_filter=status_filter
    )

@app.route('/configurations/<config_name>')
def configuration_details(config_name):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    revisions = RevisionHistory.query.filter_by(config_name=config_name).order_by(RevisionHistory.revision_date.desc()).all()
    
    return render_template('configuration_details.html', config=config, revisions=revisions)

@app.route('/configurations/<config_name>/toggle-status/<new_status>')
def toggle_status(config_name, new_status):
    config = Configuration.query.filter_by(config_name=config_name).first_or_404()
    
    config.previous_status = config.status
    config.status = new_status

    current_user = inject_user()['current_user']
    user_email = current_user.email if current_user else "system@wavelynx.com"

    rev = RevisionHistory(
        config_name=config.config_name,
        user_email=user_email,
        revision_details=f"Status changed from {config.previous_status} to {new_status}."
    )
    db.session.add(rev)
    db.session.commit()

    return redirect(url_for('configuration_details', config_name=config_name))

if __name__ == '__main__':
    app.run(debug=True, port=5000)