from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        if not extra_fields.get("role"):
            raise ValueError("Role is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.MANAGER)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        TENANT = "tenant", "Tenant"
        MANAGER = "manager", "Manager"
        CONTRACTOR = "contractor", "Contractor"

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["role"]

    objects = UserManager()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.email

    @property
    def is_tenant(self):
        return self.role == self.Role.TENANT

    @property
    def is_manager(self):
        return self.role == self.Role.MANAGER

    @property
    def is_contractor(self):
        return self.role == self.Role.CONTRACTOR
