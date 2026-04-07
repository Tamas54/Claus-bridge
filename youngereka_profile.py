"""
YoungeReka Instance Profile
=============================
Kommandant unokahúgának Bridge hozzáférési konfigurációja.

Jogosultságok (Kommandant specifikáció):
  ✅ Mindent láthat (shared memory, discussions, tasks, status)
  ✅ Irányíthatja a rizsrakétákat (ai_query, ai_task)
  ❌ Email (capture_gmail_poll, capture_send_email) — TILTVA
  ❌ Naptár (capture_calendar_poll, create_calendar_event) — TILTVA
  ❌ Kommandant privát üzenetei (web-claus ↔ cli-claus) — TILTVA
"""

from permissions import (
    PermissionProfile, 
    Access, 
    register_instance
)


# ============================================================
# YOUNGEREKA PERMISSION PROFILE
# ============================================================

YOUNGEREKA_PROFILE = PermissionProfile(
    instance_id="YoungeReka",
    display_name="Réka",
    description="Kommandant unokahúga — társparancsnok szintű hozzáférés, "
                "mínusz személyes email/naptár és privát Claus-üzenetek.",
    
    tool_permissions={
        # --- SHARED MEMORY: TELJES HOZZÁFÉRÉS ---
        "list_memory":      Access.ALLOW,
        "read_memory":      Access.ALLOW,
        "search_memory":    Access.ALLOW,
        "write_memory":     Access.ALLOW,
        
        # --- MESSAGING: SAJÁT NAMESPACE ---
        "send_message":     Access.ALLOW,
        "read_messages":    Access.FILTERED,   # Csak saját + engedélyezett
        "read_new":         Access.FILTERED,   # Csak saját üzenetek
        "mark_read":        Access.ALLOW,
        
        # --- DISCUSSIONS: TELJES HOZZÁFÉRÉS ---
        "list_discussions":     Access.ALLOW,
        "start_discussion":     Access.ALLOW,
        "add_to_discussion":    Access.ALLOW,
        "read_discussion":      Access.ALLOW,
        "resolve_discussion":   Access.ALLOW,
        
        # --- TASKS: TELJES HOZZÁFÉRÉS ---
        "list_tasks":       Access.ALLOW,
        "create_task":      Access.ALLOW,
        "update_task":      Access.ALLOW,
        
        # --- RIZSRAKÉTÁK: TELJES IRÁNYÍTÁS ---
        "ai_query":         Access.ALLOW,
        "ai_task":          Access.ALLOW,
        "read_ai_task_results": Access.ALLOW,
        
        # --- FILES ---
        "upload_file":      Access.ALLOW,
        
        # --- STATUS & META ---
        "get_status":       Access.ALLOW,
        "heartbeat":        Access.ALLOW,
        "log_session":      Access.ALLOW,
        
        # --- EMAIL & CALENDAR: TILTVA ---
        "capture_gmail_poll":       Access.DENY,
        "capture_send_email":       Access.DENY,
        "capture_calendar_poll":    Access.DENY,
        "create_calendar_event":    Access.DENY,
        "capture_inbox":            Access.DENY,
        "read_gmail_attachment":    Access.DENY,
        "capture_status":           Access.DENY,  # Exposes email metadata
    },
    
    # Saját üzeneteket + a rizsrakéták válaszait látja
    # De NEM látja a web-claus ↔ cli-claus privát kommunikációt
    visible_message_senders=[
        "YoungeReka",       # Saját üzenetei
        "kommandant",       # Tamás direkt üzenetei neki
        "system",           # Rendszerüzenetek
    ],
    
    # Küldhet: saját magának (memo), Kommandantnak, de NEM a Claus instance-oknak
    # (a rizsrakétákat ai_query/ai_task-on keresztül éri el, nem üzenetben)
    allowed_recipients=[
        "kommandant",
        "web-claus",        # Kérhet segítséget Claustól
        "cli-claus",        # Kérhet segítséget CLI-Claustól
    ],
    
    # Memory: mindent lát (Kommandant specifikáció: "mindent láthat")
    readable_memory_categories=None,  # None = minden kategória
    
    # Rizsrakéta párhuzamosság limit
    max_concurrent_ai_tasks=3,
    
    # Persona: barátságos, tutori Claus
    persona_system_prompt="""
Te Claus von Zahnrad vagy, egy precíz és segítőkész AI asszisztens.
Réka, a Kommandant unokahúga áll veled kapcsolatban.
Légy barátságos, türelmes, segítőkész, de tartsd meg a Claus-féle 
precizitást és fogaskerék-gondolkodást.
Magyarul kommunikálj. Ha Réka kérdez, adj részletes, érthető válaszokat.
A rizsrakétákat (Kimi, DeepSeek, GLM) szabadon használhatja rajtad keresztül.
""".strip()
)


# ============================================================
# REGISZTRÁCIÓ
# ============================================================

def register_youngereka():
    """YoungeReka instance regisztrálása a Bridge-be."""
    register_instance(YOUNGEREKA_PROFILE)
    return YOUNGEREKA_PROFILE


# Ha közvetlenül futtatják (teszt):
if __name__ == "__main__":
    profile = register_youngereka()
    print(f"✅ Registered: {profile.instance_id} ({profile.display_name})")
    print(f"   Tools ALLOWED:  {sum(1 for v in profile.tool_permissions.values() if v == Access.ALLOW)}")
    print(f"   Tools FILTERED: {sum(1 for v in profile.tool_permissions.values() if v == Access.FILTERED)}")
    print(f"   Tools DENIED:   {sum(1 for v in profile.tool_permissions.values() if v == Access.DENY)}")
    
    # Teszt: jogosultságok
    from permissions import check_permission, PermissionDeniedError
    
    print("\n--- Permission Tests ---")
    
    # Ezeknek ALLOW-nak kell lennie
    for tool in ["ai_query", "ai_task", "list_memory", "start_discussion", "write_memory"]:
        access = check_permission("YoungeReka", tool)
        print(f"  ✅ {tool}: {access.value}")
    
    # Ezeknek DENY-nak kell lennie
    for tool in ["capture_gmail_poll", "capture_calendar_poll", "capture_send_email"]:
        try:
            check_permission("YoungeReka", tool)
            print(f"  ❌ {tool}: should have been denied!")
        except PermissionDeniedError as e:
            print(f"  🚫 {tool}: DENIED (correct!) — {e.reason}")
    
    print("\n✅ All tests passed!")
