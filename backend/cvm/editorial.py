"""The editorial (news-desk) long format: the single rule that decides when a video is made in it.

Only long YouTube renders (profile "youtube", 16:9) of a project whose presenter is "editorial" (the project's
choice, else the channel's default), in pt-BR or en-US, for the narrated styles (explainer, top_list, dark). Shorts keep the vertical editorial header; ar-AE stays on
the current visuals (the cards have no Arabic font / RTL layout yet)."""
from sqlmodel import Session

from cvm.models import Project

LANGUAGES = ("pt-BR", "en-US")
PROFILE = "youtube"
# narrated styles only: live/react/kids/lesson/review carry recorded audio or character lines (no anchors to place)
EDITORIAL_STYLES = ("explainer", "top_list", "dark")


def wanted(session: Session, project: Project, language: str, profile: str) -> bool:
    from cvm.render.compose import presenter_mode

    return (profile == PROFILE and language in LANGUAGES and str(project.style) in EDITORIAL_STYLES
            and presenter_mode(session, project) == "editorial")


def long_for(session: Session, project: Project, language: str) -> bool:
    """The project renders a YouTube 16:9 video in this language and it is editorial: its script needs
    `editorial.cenas`."""
    from cvm.render.compose import platforms_for

    return PROFILE in platforms_for(session, project) and wanted(session, project, language, PROFILE)


def long_languages(session: Session, project: Project) -> list[str]:
    return [lang for lang in project.languages or [] if long_for(session, project, lang)]
