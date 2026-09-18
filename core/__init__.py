# core — ядро SWAGcleaner: модели, сканер, бэкапы, советник, чистка, duplicates
from core.scanner import SystemScanner
from core.backup import BackupStore
from core.ports import InstalledProvider, SystemProvider, TrashProvider, ElevateProvider
from core.advisor import Advisor, AdvisorRule, BloatwareExplorerRule, TaskPrioritizer
from core.executor import Executor
from core.cleaner import Cleaner, CleanCandidate
from core.services import WindowsServiceController, ServiceInfo
from core.startup import StartupManager, StartupEntry, read_startup
from core.apps import WindowsInstalledProvider, KNOWN_APPS
from core.processes import get_running_processes
from core.models import AppInfo, Plan
from core.dedup import DuplicateScanner
