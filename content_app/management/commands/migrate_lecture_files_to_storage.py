"""
Management command to migrate legacy lecture files from FileData to SourceFile storage.
Transfers binary data to disk storage and clears the database blob column.

Run with: python manage.py migrate_lecture_files_to_storage
"""
import os
from io import BytesIO
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from content_app.models import LectureMaterial


class Command(BaseCommand):
    help = 'Migrate lecture files from database BinaryField to file storage'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without making changes',
        )
        parser.add_argument(
            '--keep-db-data',
            action='store_true',
            help='Keep FileData in database after migration (default: clear it)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        keep_db_data = options.get('keep_db_data', False)

        # Find lectures with FileData but no SourceFile
        legacy_materials = LectureMaterial.objects.filter(
            FileData__isnull=False,
            SourceFile__exact='',
        ).exclude(FileData=b'')

        count = legacy_materials.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS('No lectures to migrate.'))
            return

        self.stdout.write(f'Found {count} lectures with legacy FileData.')

        migrated = 0
        failed = 0

        for material in legacy_materials:
            try:
                if not material.FileData:
                    continue

                # Generate storage file name
                base_name = os.path.splitext(material.OriginalFileName or f'lecture_{material.LectureID}')[0]
                ext = os.path.splitext(material.OriginalFileName or '')[1] or ''
                storage_name = f'lecture_materials/{base_name}_{material.LectureID}{ext}'

                if dry_run:
                    self.stdout.write(
                        f'  [{material.LectureID}] {material.Title} → {storage_name} '
                        f'({len(material.FileData)} bytes)'
                    )
                    migrated += 1
                    continue

                # Save to storage
                material.SourceFile.save(
                    storage_name,
                    ContentFile(material.FileData),
                    save=False,
                )

                # Clear database blob if requested
                if not keep_db_data:
                    material.FileData = b''

                material.save(update_fields=['SourceFile', 'FileData'])
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ [{material.LectureID}] {material.Title}')
                )
                migrated += 1

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ [{material.LectureID}] {material.Title}: {exc}')
                )
                failed += 1

        self.stdout.write('')
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'DRY RUN: Would migrate {migrated} lectures. Re-run without --dry-run to apply.'
                )
            )
        else:
            db_space = f' (freed ~{sum(m.FileSize for m in legacy_materials) / 1024 / 1024:.1f} MB from database)' if migrated else ''
            self.stdout.write(
                self.style.SUCCESS(f'Migrated {migrated} lectures{db_space}')
            )
            if failed:
                self.stdout.write(self.style.WARNING(f'Failed: {failed} lectures'))
