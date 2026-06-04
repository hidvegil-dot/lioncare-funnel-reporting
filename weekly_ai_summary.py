from __future__ import annotations

from typing import Any


def build_weekly_ai_summary(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    data_quality = metrics.get("data_quality", {})
    lead_to_booking = float(metrics.get("lead_to_booking_rate", 0))
    booking_to_show = float(metrics.get("booking_to_show_rate", 0))
    show_to_close = float(metrics.get("show_to_close_rate", 0))
    new_leads = int(metrics.get("new_leads", 0))
    bookings = int(metrics.get("bookings", 0))
    showed = int(metrics.get("showed", 0))
    won = int(metrics.get("won", 0))
    unassigned = int(data_quality.get("unassigned_leads", 0))
    unknown_status = int(data_quality.get("unknown_status_count", 0))

    bottleneck = "leadtermelés"
    main_problem = "Kevés új GHL lead érkezett a vizsgált héten."
    if lead_to_booking >= 30 and booking_to_show < 50:
        bottleneck = "show rate"
        main_problem = "A foglalás után túl sok ügyfél nem jut el megjelenésig."
    elif showed > 0 and show_to_close < 20:
        bottleneck = "zárás"
        main_problem = "A megjelent ügyfelekből nem lesz elég szerződés."
    elif bookings == 0 and new_leads > 0:
        bottleneck = "booking"
        main_problem = "Van leadtermelés, de nem alakul foglalássá."
    elif unassigned > max(3, new_leads * 0.2):
        bottleneck = "delegálási probléma"
        main_problem = "Túl sok lead marad tanácsadó nélkül."
    elif unknown_status > max(3, new_leads * 0.2):
        bottleneck = "mérési / státuszolási hiba"
        main_problem = "Túl sok leadnél hiányos a CRM státusz."

    works = (
        "A leadtermelés és a booking alapfolyamat működik."
        if new_leads > 0 and lead_to_booking >= 30
        else "A heti adatok alapján a leadtermelés vagy booking folyamat még nem stabil."
    )
    money_loss = (
        "A veszteség fő helye a foglalás utáni megjelenés és a zárás."
        if booking_to_show < 50 or show_to_close < 20
        else "A fő veszteség nem egyértelmű, a következő hétben a státuszfegyelmet érdemes figyelni."
    )
    opportunity = _main_opportunity(bottleneck)
    actions = _recommended_actions(bottleneck)
    data_quality_note = _data_quality_note(data_quality)

    return {
        "main_bottleneck": bottleneck,
        "main_problem": main_problem,
        "main_opportunity": opportunity,
        "what_works": works,
        "money_loss": money_loss,
        "recommended_actions": actions,
        "crm_data_quality_note": data_quality_note,
        "ceo_summary": (
            f"A hét fő válasza: {works} {money_loss} "
            f"A fő szűk keresztmetszet: {bottleneck}. "
            f"A következő héten a fókusz: {actions[0].lower()}"
        ),
        "keep": "Tartsuk a GHL-alapú heti funnel mérést és a tanácsadói bontást.",
        "scale": "Csak akkor skálázzuk a hirdetést, ha a show és zárási arány javul.",
        "stop": "Állítsuk le azokat a folyamatokat, amelyek leadet hoznak, de nem jutnak foglalásig vagy megjelenésig.",
        "sales_fix": "Napi no-show és zárási follow-up ellenőrzés tanácsadónként.",
        "crm_fix": "Minden leadnél kötelező assigned user, lead status és appointment status.",
        "ops_tasks": "Ossz ki heti felelőst a no-show visszahívásra, státuszjavításra és zárási utánkövetésre.",
    }


def advisor_summary(row: dict[str, Any]) -> str:
    return (
        f"{row.get('advisor')}: {row.get('new_leads', 0)} lead, "
        f"{row.get('bookings', 0)} foglalás, {row.get('showed', 0)} megjelent, "
        f"{row.get('no_show', 0)} no-show, {row.get('cancelled', 0)} törölt/lemondott, "
        f"{row.get('won', 0)} szerződés. "
        f"Lead→foglalás {row.get('lead_to_booking_rate', 0)}%, "
        f"foglalás→megjelent {row.get('booking_to_show_rate', 0)}%, "
        f"megjelent→szerződés {row.get('show_to_close_rate', 0)}%."
    )


def _main_opportunity(bottleneck: str) -> str:
    if bottleneck == "show rate":
        return "No-show csökkentés emlékeztetőkkel, visszahívással és időpont-megerősítéssel."
    if bottleneck == "zárás":
        return "Ajánlat, zárási script és meeting utáni follow-up javítása."
    if bottleneck == "booking":
        return "Lead utáni első reakcióidő és booking follow-up szigorítása."
    if bottleneck == "delegálási probléma":
        return "Automatikus vagy napi manuális lead-delegálási kontroll."
    if bottleneck == "mérési / státuszolási hiba":
        return "CRM státuszfegyelem javítása és kötelező státuszmezők ellenőrzése."
    return "Leadforrások és landingek minőségi bontásának ellenőrzése."


def _recommended_actions(bottleneck: str) -> list[str]:
    if bottleneck == "show rate":
        return ["No-show csökkentő protokoll bevezetése", "Tanácsadónkénti megjelenési arány napi ellenőrzése", "Lemondott időpontok újrafoglalási folyamata"]
    if bottleneck == "zárás":
        return ["Zárási beszélgetések visszanézése", "Ajánlat és objection handling javítása", "Meeting utáni 24 órás follow-up kötelezővé tétele"]
    if bottleneck == "booking":
        return ["Lead után 5 percen belüli kapcsolatfelvétel mérése", "Booking follow-up sablonok frissítése", "Nem foglalt leadek napi listázása"]
    if bottleneck == "delegálási probléma":
        return ["Minden új lead automatikus delegálása", "Nincs delegálva lista napi lezárása", "Tanácsadói terhelés újraosztása"]
    if bottleneck == "mérési / státuszolási hiba":
        return ["Kötelező státuszfrissítés minden meeting után", "Ismeretlen státuszú leadek javítása", "Heti CRM adatminőségi audit"]
    return ["Leadforrás bontás tisztítása", "Gyenge források leállítása", "Erős landingek megtartása"]


def _data_quality_note(data_quality: dict[str, Any]) -> str:
    notes = []
    if data_quality.get("unassigned_leads", 0):
        notes.append(f"{data_quality['unassigned_leads']} lead nincs delegálva.")
    if data_quality.get("unknown_status_count", 0):
        notes.append(f"{data_quality['unknown_status_count']} leadnél hiányzik a státusz.")
    if not data_quality.get("opportunities_available", False):
        notes.append("Az opportunity lista nem volt elérhető, ezért won/lost értékek nullák lehetnek.")
    return " ".join(notes) or "Nincs kritikus adatminőségi jelzés."
