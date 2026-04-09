"""
Bridge Permission Middleware
============================
Claus-Bridge MCP granulált jogosultságkezelés.

Minden instance-hoz egy PermissionProfile tartozik, ami tool-szinten
szabályozza a hozzáférést (ALLOW / DENY / FILTERED).

Architekturális elv: "Alles was nicht erlaubt ist, ist verboten."
(Ami nincs engedélyezve, az tiltva van.)
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger("bridge.permissions")


# ============================================================
# 1. PERMISSION LEVELS
# ============================================================

class Access(Enum):
    ALLOW = "allow"          # Teljes hozzáférés
    DENY = "deny"            # Tiltva — 403-at kap
    FILTERED = "filtered"    # Hozzáfér, de szűrt adatot kap


# ============================================================
# 2. PERMISSION PROFILE
# ============================================================

@dataclass
class PermissionProfile:
    """Egy instance jogosultsági profilja."""
    
    instance_id: str
    display_name: str
    description: str = ""
    
    # Tool-szintű jogosultságok: tool_name -> Access
    tool_permissions: dict[str, Access] = field(default_factory=dict)
    
    # Melyik instance-ok üzeneteit láthatja
    visible_message_senders: list[str] = field(default_factory=list)
    
    # Melyik instance-oknak küldhet üzenetet
    allowed_recipients: list[str] = field(default_factory=list)
    
    # Memory namespace izolálás (None = shared, string = izolált prefix)
    memory_namespace: Optional[str] = None
    
    # Milyen memory kategóriákat olvashat (None = mind)
    readable_memory_categories: Optional[list[str]] = None
    
    # Maximális AI task párhuzamosság
    max_concurrent_ai_tasks: int = 3
    
    # System prompt / persona override
    persona_system_prompt: Optional[str] = None
    
    def can_access(self, tool_name: str) -> Access:
        """Adott tool-hoz milyen hozzáférése van."""
        return self.tool_permissions.get(tool_name, Access.DENY)
    
    def can_send_to(self, recipient: str) -> bool:
        """Küldhet-e üzenetet adott recipientnek."""
        return recipient in self.allowed_recipients
    
    def can_see_messages_from(self, sender: str) -> bool:
        """Láthatja-e adott sender üzeneteit."""
        return sender in self.visible_message_senders


# ============================================================
# 3. INSTANCE REGISTRY — a "Hauptquartier"
# ============================================================

# Alap instance-ok (Kommandant eredeti csapatai)
CORE_INSTANCES = {"web-claus", "cli-claus", "kommandant", "feldwebel"}

# Minden regisztrált instance profilja
INSTANCE_PROFILES: dict[str, PermissionProfile] = {}


def register_instance(profile: PermissionProfile) -> None:
    """Új instance regisztrálása a Bridge-be."""
    INSTANCE_PROFILES[profile.instance_id] = profile
    logger.info(f"Instance registered: {profile.instance_id} ({profile.display_name})")


def get_profile(instance_id: str) -> Optional[PermissionProfile]:
    """Instance profiljának lekérése."""
    return INSTANCE_PROFILES.get(instance_id)


def is_core_instance(instance_id: str) -> bool:
    """Core instance-e (korlátlan hozzáférés)."""
    return instance_id in CORE_INSTANCES


# ============================================================
# 4. PERMISSION MIDDLEWARE
# ============================================================

class PermissionDeniedError(Exception):
    """Hozzáférés megtagadva."""
    def __init__(self, instance_id: str, tool_name: str, reason: str = ""):
        self.instance_id = instance_id
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(
            f"ZUGANG VERWEIGERT: {instance_id} → {tool_name}"
            + (f" ({reason})" if reason else "")
        )


def check_permission(instance_id: str, tool_name: str, **kwargs) -> Access:
    """
    Fő permission check — minden tool hívás előtt lefut.
    
    Returns:
        Access.ALLOW — mehet
        Access.FILTERED — mehet, de szűrt eredményt kap
    
    Raises:
        PermissionDeniedError — ha DENY
    """
    # Core instance-ok mindenhez hozzáférnek
    if is_core_instance(instance_id):
        return Access.ALLOW
    
    profile = get_profile(instance_id)
    if profile is None:
        raise PermissionDeniedError(
            instance_id, tool_name, 
            "Unbekannter Soldat — instance not registered"
        )
    
    access = profile.can_access(tool_name)
    
    if access == Access.DENY:
        raise PermissionDeniedError(
            instance_id, tool_name,
            "Tool access denied by permission profile"
        )
    
    # Tool-specifikus extra ellenőrzések
    if tool_name == "send_message":
        recipient = kwargs.get("recipient", "")
        if not profile.can_send_to(recipient):
            raise PermissionDeniedError(
                instance_id, tool_name,
                f"Cannot send messages to {recipient}"
            )
    
    if tool_name == "read_messages" or tool_name == "read_new":
        # Filtered: csak a saját és az engedélyezett üzeneteket látja
        return Access.FILTERED
    
    return access


def filter_messages(instance_id: str, messages: list[dict]) -> list[dict]:
    """Üzenetek szűrése az instance jogosultságai alapján."""
    if is_core_instance(instance_id):
        return messages
    
    profile = get_profile(instance_id)
    if profile is None:
        return []
    
    return [
        msg for msg in messages
        if (
            msg.get("sender") == instance_id
            or msg.get("recipient") == instance_id
            or profile.can_see_messages_from(msg.get("sender", ""))
        )
    ]


def filter_memory_results(instance_id: str, entries: list[dict]) -> list[dict]:
    """Memory bejegyzések szűrése kategória alapján."""
    if is_core_instance(instance_id):
        return entries
    
    profile = get_profile(instance_id)
    if profile is None:
        return []
    
    if profile.readable_memory_categories is None:
        return entries  # Mindent lát
    
    return [
        entry for entry in entries
        if entry.get("category") in profile.readable_memory_categories
    ]


# ============================================================
# 5. FASTAPI MIDDLEWARE INTEGRÁCIÓ
# ============================================================

def create_permission_middleware():
    """
    FastAPI middleware factory.
    
    Használat a Bridge main.py-ban:
    
        from permissions import create_permission_middleware
        app.middleware("http")(create_permission_middleware())
    """
    async def permission_middleware(request, call_next):
        # MCP tool hívások esetén az instance_id-t
        # a tool paraméterekből vagy headerből vesszük
        # A tényleges ellenőrzés a tool handler-ekben történik
        # (mert ott van kontextus a paraméterekről)
        response = await call_next(request)
        return response
    
    return permission_middleware


# ============================================================
# 6. DECORATOR TOOL HANDLEREKHEZ
# ============================================================

def requires_permission(tool_name: str):
    """
    Decorator a Bridge tool handler függvényekhez.
    
    Használat:
        @requires_permission("send_message")
        async def handle_send_message(instance: str, **kwargs):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            instance_id = kwargs.get("instance") or kwargs.get("sender") or "unknown"
            access = check_permission(instance_id, tool_name, **kwargs)
            
            result = await func(*args, **kwargs)
            
            # Ha FILTERED, akkor szűrjük az eredményt
            if access == Access.FILTERED:
                if isinstance(result, list):
                    result = filter_messages(instance_id, result)
            
            return result
        
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    
    return decorator
