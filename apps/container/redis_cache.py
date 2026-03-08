import json
from django.core.cache import cache
from django.conf import settings

class UserContainerCache:
    PREFIX = "user_container:"
    EXPIRE_TIME = 3600*2

    @classmethod
    def get_key(cls, user_id, challenge_uuid):
        return f"{cls.PREFIX}{user_id}:{challenge_uuid}"

    @classmethod
    def set(cls, user_container):
        key = cls.get_key(user_container.user.id, user_container.challenge_uuid)
        data = {
            "id": user_container.id,
            "user_id": user_container.user.id,
            "challenge_id": user_container.challenge.id,
            "challenge_uuid": str(user_container.challenge_uuid),
            "docker_engine_id": user_container.docker_engine.id,
            "container_id": user_container.container_id,
            "ip_address": user_container.ip_address,
            "port": user_container.port,
            "domain": user_container.domain,
            "created_at": user_container.created_at.isoformat(),
            "expires_at": user_container.expires_at.isoformat()
        }
        cache.set(key, json.dumps(data), timeout=cls.EXPIRE_TIME)

    @classmethod
    def get(cls, user_id, challenge_uuid):
        key = cls.get_key(user_id, challenge_uuid)
        data = cache.get(key)
        if data:
            return json.loads(data)
        return None

    @classmethod
    def delete(cls, user_id, challenge_uuid):
        key = cls.get_key(user_id, challenge_uuid)
        cache.delete(key)