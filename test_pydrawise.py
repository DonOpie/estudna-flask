import asyncio
from datetime import datetime
from pydrawise import Auth, Hydrawise
from pydrawise import helpers  # 📌 pro přímý přístup k API

HW_USER = "viskot@servis-zahrad.cz"
HW_PASS = "Poklop1234*"

async def run_test():
    output = []

    # Aktuální čas na serveru
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output.append(f"🕒 Aktuální čas serveru: {now_str}")

    try:
        h = Hydrawise(Auth(HW_USER, HW_PASS))

        # Controllers
        controllers = await h.get_controllers()
        if not controllers:
            return "❌ Žádný controller nebyl nalezen."
        controller = controllers[0]
        output.append(f"➡️ Controller: {controller.name} (ID {controller.id})")

        # Zones
        zones = await h.get_zones(controller)
        if not zones:
            return "❌ Žádné zóny nebyly nalezeny."
        zone = zones[0]
        output.append("➡️ Zones: " + ", ".join([z.name for z in zones]))

        # Start zone (5 minut pro jistotu)
        try:
            res = await h.start_zone(zone, custom_run_duration=300)
            output.append(f"✅ start_zone spuštěno pro zónu {zone.name} (5 min)")
            output.append(f"🔎 Odpověď API (pydrawise): {res}")
        except Exception as e:
            output.append(f"❌ Chyba start_zone: {e}")

        # Stop zone
        try:
            res2 = await h.stop_zone(zone)
            output.append(f"✅ stop_zone provedeno pro zónu {zone.name}")
            output.append(f"🔎 Odpověď API (pydrawise): {res2}")
        except Exception as e:
            output.append(f"❌ Chyba stop_zone: {e}")

        # 📌 Přímý dotaz na Hydrawise API (JSON)
        try:
            token = (await h._auth.token()).replace("Bearer ", "")
            cust = helpers.customer_details(token)
            sched = helpers.status_schedule(token)
            output.append("📡 customer_details JSON:")
            output.append(str(cust))
            output.append("📡 status_schedule JSON:")
            output.append(str(sched))
        except Exception as e:
            output.append(f"❌ Chyba při načítání detailů: {e}")

    except Exception as e:
        output.append(f"❌ Chyba pydrawise: {e}")

    return "\n".join(output)

def main():
    return asyncio.run(run_test())

if __name__ == "__main__":
    print(main())
