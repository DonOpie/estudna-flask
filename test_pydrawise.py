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
        auth = Auth(API_KEY)
        hw = Hydrawise(auth)

        output = [f"🕒 Aktuální čas serveru: {now}"]

        controllers = await hw.get_controllers()
        for controller in controllers:
            output.append(f"➡️ Controller: {controller.name} (ID {controller.id})")

            zones = await hw.get_zones(controller)
            for zone in zones:
                output.append(f"🌱 Zone: {zone.name} (ID {zone.id})")

                # Spustíme zónu na 5 minut
                try:
                    await hw.start_zone(zone, custom_run_duration=300)
                    output.append(f"✅ start_zone spuštěno pro zónu {zone.name} (5 min)")
                except Exception as e:
                    output.append(f"❌ Chyba start_zone: {e}")

            # Spustíme všechny zóny na 5 minut
            try:
                await hw.start_all_zones(controller, custom_run_duration=300)
                output.append("✅ start_all_zones spuštěno (5 min)")
            except Exception as e:
                output.append(f"❌ Chyba start_all_zones: {e}")

            # Zkontrolujeme, které zóny běží
            zones_after = await hw.get_zones(controller)
            running = [z for z in zones_after if z.scheduled_runs.current_run is not None]
            if running:
                for r in running:
                    output.append(
                        f"▶️ Zóna {r.name} běží, zbývá {r.scheduled_runs.current_run.remaining_time}"
                    )
            else:
                output.append("⏹ Žádná zóna neběží")

        return output

    result = asyncio.run(main())
    return "<br>".join(result)
