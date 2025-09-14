import asyncio
from datetime import datetime
from pydrawise import Auth, Hydrawise

EMAIL = "viskot@servis-zahrad.cz"
PASSWORD = "Poklop1234*"

async def main():
    print("🕒 Server time:", datetime.now())

    auth = Auth(EMAIL, PASSWORD)
    hw = Hydrawise(auth)

    controllers = await hw.get_controllers(fetch_zones=True)
    controller = controllers[0]
    print(f"➡️ Controller: {controller.name} (ID {controller.id})")

    zones = await hw.get_zones(controller)
    print("➡️ Zones:")
    for z in zones:
        print(f"   - {z.name} (ID {z.id})")

    # 🔹 TEST: spusť všechny zóny na 5 minut
    print("\n▶️ Spouštím všechny zóny na 5 minut…")
    await hw.start_all_zones(controller, custom_run_duration=300)

    # 🔹 Znovu načíst zóny a vypsat stav
    zones = await hw.get_zones(controller)
    print("\n📡 Stav zón po start_all_zones:")
    for z in zones:
        running = "běží" if z.scheduled_runs.current_run else "stop"
        print(f"   - {z.name}: {running}")

    # 🔹 Počkej 10s a znovu ověř stav
    await asyncio.sleep(10)
    zones = await hw.get_zones(controller)
    print("\n📡 Stav zón po 10s:")
    for z in zones:
        running = "běží" if z.scheduled_runs.current_run else "stop"
        print(f"   - {z.name}: {running}")

if __name__ == "__main__":
    asyncio.run(main())
