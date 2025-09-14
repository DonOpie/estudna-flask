from flask import Blueprint
from datetime import datetime
import asyncio
from pydrawise import Auth, Hydrawise

bp = Blueprint("pydrawise", __name__)

API_KEY = "d9c8-2212-cd08-6bb5"


@bp.route("/pydrawise")
def pydrawise_test():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async def main():
        lines = []
        try:
            auth = Auth(API_KEY)
            hw = Hydrawise(auth)

            lines.append(f"🕒 Aktuální čas serveru: {now}")

            controllers = await hw.get_controllers()
            for controller in controllers:
                lines.append(f"➡️ Controller: {controller.name} (ID {controller.id})")

                zones = await hw.get_zones(controller)
                for zone in zones:
                    lines.append(f"🌱 Zone: {zone.name} (ID {zone.id})")

                    # Spustíme zónu na 5 minut
                    try:
                        await hw.start_zone(zone, custom_run_duration=300)
                        lines.append(f"✅ start_zone spuštěno pro zónu {zone.name} (5 min)")
                    except Exception as e:
                        lines.append(f"❌ Chyba start_zone: {e}")

                # Spustíme všechny zóny na 5 minut
                try:
                    await hw.start_all_zones(controller, custom_run_duration=300)
                    lines.append("✅ start_all_zones spuštěno (5 min)")
                except Exception as e:
                    lines.append(f"❌ Chyba start_all_zones: {e}")

                # Kontrola, co běží
                try:
                    zones_after = await hw.get_zones(controller)
                    running = [z for z in zones_after if z.scheduled_runs.current_run is not None]
                    if running:
                        for r in running:
                            lines.append(
                                f"▶️ Zóna {r.name} běží, zbývá {r.scheduled_runs.current_run.remaining_time}"
                            )
                    else:
                        lines.append("⏹ Žádná zóna neběží")
                except Exception as e:
                    lines.append(f"❌ Chyba při kontrole běžících zón: {e}")

        except Exception as e:
            lines.append(f"❌ Neošetřená chyba: {e}")

        return lines

    try:
        result = asyncio.run(main())
    except Exception as e:
        result = [f"❌ Chyba při spuštění asyncio.run: {e}"]

    return "<br>".join(result)
