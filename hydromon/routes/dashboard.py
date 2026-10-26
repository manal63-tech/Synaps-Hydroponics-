"""Dashboard (HTML) routes."""
from flask import Blueprint, render_template

from ..rule_config import get_rule_metadata

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/", methods=["GET"])
def dashboard():
    return render_template("index.html", rule_metadata=get_rule_metadata())
