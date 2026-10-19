"""Checks a CV tailored to a vacancy against the candidate's own CV: a tailored CV may reorder, shorten and rephrase,
but every number and every tool / technology in it must already be in the original CV or the profile."""

from __future__ import annotations

import re

TOOLS = {
    # testing
    "appium", "selenium", "cypress", "playwright", "webdriverio", "jmeter", "postman", "charles", "fiddler", "testrail",
    "zephyr", "qase", "jira", "confluence", "browserstack", "devtools", "logcat", "xcode", "android studio", "swagger",
    "soapui", "testflight", "firebase", "allure", "pytest", "junit", "testng", "cucumber", "gherkin", "k6", "locust",
    # languages and platforms
    "java", "python", "javascript", "typescript", "kotlin", "swift", "c#", "go", "php", "ruby", "sql", "bash", "html", "css",
    "react", "angular", "vue", "node.js", "django", ".net", "flutter", "react native", "unity",
    # data and infrastructure
    "postgresql", "mysql", "mongodb", "redis", "kafka", "graphql", "rest", "grpc", "websocket", "docker", "kubernetes",
    "jenkins", "github actions", "gitlab", "git", "aws", "azure", "gcp", "linux",
    # process
    "scrum", "kanban", "istqb",
}
_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def tools(text: str) -> set[str]:
    low = text.lower()
    return {t for t in TOOLS if re.search(rf"(?<![\w.+#-]){re.escape(t)}(?![\w+#-])", low)}


def unverified(tailored: str, source: str) -> dict[str, list[str]]:
    """Numbers (except years) and tools in the tailored CV that the source never mentions."""
    known = {n.replace(",", ".") for n in _NUMBER.findall(source)}
    numbers = sorted({n for n in _NUMBER.findall(tailored) if n.replace(",", ".") not in known and not _YEAR.fullmatch(n)})
    return {"numbers": numbers, "tools": sorted(tools(tailored) - tools(source))}
