import asyncio
from pydrawise import Auth, Hydrawise

# Přihlašovací údaje k Hydrawise účtu
HW_USER = "viskot@servis-zahrad.cz"
HW_PASS = "Poklop1234*"   # správné heslo

async def main():
    # Přihlášení a vytvoření klienta
    h = Hydrawise(Auth(HW_USER, HW_PASS))

    # 1) Získání seznamu controllerů
    controllers = await h.get_controllers()
    print("➡️ Controllers:", controllers)

    if not controllers:
        print("❌ Žádný controller nebyl nalezen.")
        return
    controller = controllers[0]

    # 2) Získání seznamu zón pro controller
    zones = await h.get_zones(controller)
    print("➡️ Zones:", zones)

    if not zones:
        print("❌ Žádné zóny nebyly nalezeny.")
        return
    zone = zones[0]

    # 3) Spuštění první zóny na 60 sekund
    print(f"▶️ Spouštím zónu: {zone.name} (ID {zone.id}) na 60 sekund...")
    try:
        await h.start_zone(zone, custom_run_duration=60)
        print("✅ start_zone proběhlo OK")
    except Exception as e:
        print("❌ Chyba při start_zone:", e)

    # 4) Zastavení zóny
    print(f"⏹ Zastavuji zónu: {zone.name} (ID {zone.id})...")
    try:
        await h.stop_zone(zone)
        print("✅ stop_zone proběhlo OK")
    except Exception as e:
        print("❌ Chyba při stop_zone:", e)


if __name__ == "__main__":
    asyncio.run(main())
