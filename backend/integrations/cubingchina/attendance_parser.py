import re
from dataclasses import replace
from datetime import date
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from apps.competitors.geography import continent_for_country_code, country_code_from_name
from integrations.attendance_types import SourceCompetition, SourceRegistrant, valid_wca_id

WCA_COMPETITION_PATH = re.compile(r"/competitions/([^/?#]+)")
PERSON_PATH = re.compile(r"/results/person/([^/?#]+)")


def _date_range(value: str) -> tuple[date, date]:
    parts = [part.strip() for part in value.split("~", 1)]
    start = date.fromisoformat(parts[0])
    if len(parts) == 1:
        return start, start
    end_text = parts[1]
    if re.fullmatch(r"\d{2}", end_text):
        end_text = f"{start.year:04d}-{start.month:02d}-{end_text}"
    elif re.fullmatch(r"\d{2}-\d{2}", end_text):
        end_text = f"{start.year:04d}-{end_text}"
    return start, date.fromisoformat(end_text)


def _competition_slug(href: str) -> str:
    path = urlparse(href).path.rstrip("/")
    if "/competition/" in path:
        return path.rsplit("/competition/", 1)[1]
    if "/live/" in path:
        return path.rsplit("/live/", 1)[1]
    raise ValueError(f"Unsupported CubingChina competition link: {href!r}")


def parse_competition_index(html: str) -> list[SourceCompetition]:
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (
            candidate
            for candidate in soup.select("table")
            if "Competition Name" in candidate.get_text(" ", strip=True)
            and "Date" in candidate.get_text(" ", strip=True)
        ),
        None,
    )
    if table is None:
        raise ValueError("CubingChina competition table was not found")

    competitions = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        link = cells[1].find("a", href=True)
        if link is None:
            continue
        start_date, end_date = _date_range(cells[0].get_text(" ", strip=True))
        slug = _competition_slug(link["href"])
        province = cells[2].get_text(" ", strip=True)
        country_code = {
            "Hong Kong, China": "HK",
            "Macau, China": "MO",
            "Chinese Taipei": "TW",
        }.get(province, "CN")
        competitions.append(
            SourceCompetition(
                source="cubingchina",
                source_id=slug,
                name=link.get_text(" ", strip=True),
                start_date=start_date,
                end_date=end_date,
                country_code=country_code,
                city=cells[3].get_text(" ", strip=True),
                registration_path=f"/competition/{slug}/competitors",
                source_url=f"https://cubing.com/competition/{slug}",
            )
        )
    return competitions


def parse_competition_detail(html: str, competition: SourceCompetition) -> SourceCompetition:
    soup = BeautifulSoup(html, "html.parser")
    wca_id = None
    official_link = soup.select_one('a[href*="worldcubeassociation.org/competitions/"]')
    if official_link:
        match = WCA_COMPETITION_PATH.search(official_link.get("href", ""))
        if match:
            wca_id = match.group(1)
    return replace(competition, wca_id=wca_id)


def parse_registrations(html: str) -> list[SourceRegistrant]:
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (
            candidate
            for candidate in soup.select("table")
            if candidate.select_one("th.header-username")
            and candidate.select_one("th.header-region")
        ),
        None,
    )
    if table is None:
        raise ValueError("CubingChina competitor table was not found")

    headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
    try:
        name_index = headers.index("Name")
        region_index = headers.index("Region")
    except ValueError as exc:
        raise ValueError("CubingChina competitor table columns changed") from exc

    registrations = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) <= max(name_index, region_index):
            continue
        profile = cells[name_index].find("a", href=True)
        if profile is None:
            continue
        match = PERSON_PATH.search(profile["href"])
        if not match or not valid_wca_id(match.group(1)):
            continue
        name = profile.get_text(" ", strip=True)
        country_code = country_code_from_name(cells[region_index].get_text(" ", strip=True))
        continent = continent_for_country_code(country_code)
        if not continent:
            raise ValueError(f"No continent mapping for CubingChina country {country_code!r}")
        registrations.append(
            SourceRegistrant(
                wca_id=match.group(1),
                name=name,
                country_code=country_code,
                continent=continent,
                sources={"cubingchina"},
            )
        )
    return registrations
