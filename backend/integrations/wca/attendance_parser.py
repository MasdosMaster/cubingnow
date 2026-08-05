from datetime import date

from apps.competitors.geography import normalise_continent
from integrations.attendance_types import SourceCompetition, SourceRegistrant, valid_wca_id


def parse_competitions(payload) -> list[SourceCompetition]:
    if not isinstance(payload, list):
        raise TypeError("WCA competition response must be a list")
    competitions = []
    for item in payload:
        try:
            wca_id = item["id"]
            competitions.append(
                SourceCompetition(
                    source="wca",
                    source_id=wca_id,
                    wca_id=wca_id,
                    name=item["name"].strip(),
                    start_date=date.fromisoformat(item["start_date"]),
                    end_date=date.fromisoformat(item["end_date"]),
                    country_code=item["country_iso2"].upper(),
                    city=(item.get("city") or "").strip(),
                    registration_path=f"/api/v1/competitions/{wca_id}/registrations",
                    source_url=item.get("url")
                    or f"https://www.worldcubeassociation.org/competitions/{wca_id}",
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid WCA competition payload: {item!r}") from exc
    return competitions


def parse_registrations(payload) -> list[SourceRegistrant]:
    if not isinstance(payload, list):
        raise TypeError("WCA registration response must be a list")
    registrations = []
    for item in payload:
        user = item.get("user") or {}
        wca_id = user.get("wca_id")
        if not valid_wca_id(wca_id):
            continue
        country = user.get("country") or {}
        country_code = (country.get("iso2") or user.get("country_iso2") or "").upper()
        continent = normalise_continent(country.get("continent_id"))
        name = (user.get("name") or "").strip()
        if not name or not country_code or not continent:
            raise ValueError(f"Incomplete returning WCA competitor payload: {item!r}")
        registrations.append(
            SourceRegistrant(
                wca_id=wca_id,
                name=name,
                country_code=country_code,
                continent=continent,
                sources={"wca"},
            )
        )
    return registrations
