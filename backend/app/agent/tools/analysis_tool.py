"""Analysis specialist tool exports."""
from app.agent.detection_agent import query_detection_statistics, query_detection_trends, query_system_roles, query_system_users

ANALYSIS_TOOLS = [query_detection_statistics, query_detection_trends, query_system_users, query_system_roles]
