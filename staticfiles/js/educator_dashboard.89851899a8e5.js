document.addEventListener('DOMContentLoaded', function () {
    const config = document.getElementById('ai-dashboard-config');
    const statusBanner = document.getElementById('ai-processing-status');
    const leftStack = document.getElementById('educator-left-stack');
    const classroomAnchor = document.getElementById('classroom-card-anchor');
    const summaryCard = document.querySelector('.queue-card-scroll');
    const uploadForm = document.getElementById('lecture-upload-form');
    const uploadDropzone = document.getElementById('lecture-upload-dropzone');
    const uploadStatus = document.getElementById('lecture-upload-status');
    const uploadProgressPanel = document.getElementById('lecture-upload-progress');
    const uploadProgressBar = document.getElementById('lecture-upload-progress-bar');
    const uploadProgressLabel = document.getElementById('lecture-upload-progress-label');
    const uploadFileProgressList = document.getElementById('lecture-upload-file-progress-list');
    const uploadSelectedFiles = document.getElementById('lecture-upload-selected-files');
    const uploadSubmitButton = document.getElementById('lecture-upload-submit');

    let pendingCount = 0;
    let summaryCount = 0;
    let archivedCount = 0;
    let statusUrl = '';
    let selectedFiles = [];
    let isUploading = false;

    if (config) {
        pendingCount = Number(config.dataset.pendingCount || 0);
        summaryCount = Number(config.dataset.summaryCount || 0);
        archivedCount = Number(config.dataset.archivedCount || 0);
        statusUrl = config.dataset.statusUrl || '';
    }

    const renderProcessingStatus = (count) => {
        if (!statusBanner) {
            return;
        }
        if (count > 0) {
            const noun = count === 1 ? 'lecture' : 'lectures';
            statusBanner.textContent = `AI is processing ${count} ${noun}... this section updates automatically.`;
            statusBanner.classList.remove('d-none');
            return;
        }

        statusBanner.classList.add('d-none');
    };

    const setUploadStatus = (message, kind = 'info') => {
        if (!uploadStatus) {
            return;
        }

        uploadStatus.textContent = message;
        uploadStatus.classList.remove('d-none', 'alert-info', 'alert-success', 'alert-danger', 'alert-warning');
        uploadStatus.classList.add(`alert-${kind}`);
    };

    const hideUploadStatus = () => {
        if (!uploadStatus) {
            return;
        }
        uploadStatus.classList.add('d-none');
        uploadStatus.textContent = '';
    };

    const formatBytes = (bytes) => {
        if (!Number.isFinite(bytes) || bytes <= 0) {
            return '0 B';
        }

        const units = ['B', 'KB', 'MB', 'GB'];
        let value = bytes;
        let unitIndex = 0;

        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }

        return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
    };

    const getUploadInput = () => uploadForm ? uploadForm.querySelector('input[type="file"][name="UploadFile"]') : null;
    const getTitleInput = () => uploadForm ? uploadForm.querySelector('input[name="Title"]') : null;
    const getSummaryModeInput = () => uploadForm ? uploadForm.querySelector('select[name="SummaryMode"]') : null;

    const syncUploadInput = () => {
        const input = getUploadInput();
        if (!input || !window.DataTransfer) {
            return;
        }

        const dataTransfer = new DataTransfer();
        selectedFiles.forEach((file) => dataTransfer.items.add(file));
        input.files = dataTransfer.files;
    };

    const renderSelectedFiles = () => {
        if (!uploadSelectedFiles) {
            return;
        }

        uploadSelectedFiles.innerHTML = '';

        if (!selectedFiles.length) {
            const empty = document.createElement('div');
            empty.className = 'upload-file-empty text-muted small';
            empty.textContent = 'No files selected yet.';
            uploadSelectedFiles.appendChild(empty);
            return;
        }

        selectedFiles.forEach((file, index) => {
            const item = document.createElement('div');
            item.className = 'upload-file-chip';
            item.innerHTML = `
                <div>
                    <strong>${index + 1}. ${file.name}</strong>
                    <span>${formatBytes(file.size)}</span>
                </div>
                <button type="button" class="btn btn-sm btn-outline-light upload-file-remove" data-file-index="${index}">Remove</button>
            `;
            uploadSelectedFiles.appendChild(item);
        });
    };

    const setFiles = (files) => {
        selectedFiles = Array.from(files || []);
        syncUploadInput();
        renderSelectedFiles();
        hideUploadStatus();
    };

    const setProgress = (percent, label) => {
        if (!uploadProgressPanel || !uploadProgressBar || !uploadProgressLabel) {
            return;
        }

        uploadProgressPanel.classList.remove('d-none');
        const clamped = Math.max(0, Math.min(100, Math.round(percent)));
        uploadProgressBar.style.width = `${clamped}%`;
        uploadProgressBar.setAttribute('aria-valuenow', String(clamped));
        uploadProgressLabel.textContent = label || `${clamped}%`;
    };

    const renderFileProgressRows = () => {
        if (!uploadFileProgressList) {
            return;
        }

        uploadFileProgressList.innerHTML = '';
        selectedFiles.forEach((file, index) => {
            const row = document.createElement('div');
            row.className = 'upload-file-progress-row';
            row.dataset.fileIndex = String(index);
            row.innerHTML = `
                <div class="d-flex justify-content-between align-items-center gap-2 mb-1">
                    <strong>${file.name}</strong>
                    <span class="upload-file-progress-state text-muted small">Queued</span>
                </div>
                <div class="progress" style="height: 0.6rem;">
                    <div class="progress-bar" style="width: 0%"></div>
                </div>
            `;
            uploadFileProgressList.appendChild(row);
        });
    };

    const setFileProgress = (index, percent, stateText, stateClass) => {
        if (!uploadFileProgressList) {
            return;
        }

        const row = uploadFileProgressList.querySelector(`[data-file-index="${index}"]`);
        if (!row) {
            return;
        }

        const bar = row.querySelector('.progress-bar');
        const state = row.querySelector('.upload-file-progress-state');
        if (bar) {
            const clamped = Math.max(0, Math.min(100, Math.round(percent)));
            bar.style.width = `${clamped}%`;
            bar.setAttribute('aria-valuenow', String(clamped));
        }
        if (state) {
            state.textContent = stateText;
            state.className = `upload-file-progress-state text-muted small ${stateClass || ''}`.trim();
        }
    };

    const buildTitleForFile = (baseTitle, file, totalFiles) => {
        if (totalFiles <= 1) {
            return baseTitle;
        }

        const fileStem = file.name.replace(/\.[^.]+$/, '').trim();
        return fileStem ? `${baseTitle} - ${fileStem}` : `${baseTitle} - ${file.name}`;
    };

    const readJsonResponse = async (xhr) => {
        if (xhr.responseType === 'json' && xhr.response) {
            return xhr.response;
        }

        if (typeof xhr.responseText === 'string' && xhr.responseText.length) {
            try {
                return JSON.parse(xhr.responseText);
            } catch (err) {
                return null;
            }
        }

        return null;
    };

    const uploadSingleFile = (file, index, totalFiles, baseTitle, summaryMode) => new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', uploadForm.action || window.location.href, true);
        xhr.responseType = 'json';
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

        xhr.upload.addEventListener('progress', (event) => {
            if (!event.lengthComputable) {
                return;
            }

            const percent = event.total > 0 ? (event.loaded / event.total) * 100 : 0;
            setFileProgress(index, percent, `${Math.round(percent)}%`, 'text-info');

            const overallPercent = ((index + event.loaded / event.total) / totalFiles) * 100;
            setProgress(overallPercent, `${index + 1} / ${totalFiles} files`);
        });

        xhr.onload = async () => {
            const payload = await readJsonResponse(xhr);
            if (xhr.status >= 200 && xhr.status < 300) {
                setFileProgress(index, 100, 'Done', 'text-success');
                resolve(payload);
                return;
            }

            const errorText = payload && payload.errors ? JSON.stringify(payload.errors) : `Upload failed with status ${xhr.status}.`;
            setFileProgress(index, 100, 'Failed', 'text-danger');
            reject(new Error(errorText));
        };

        xhr.onerror = () => {
            setFileProgress(index, 100, 'Failed', 'text-danger');
            reject(new Error('Network error while uploading.'));
        };

        const formData = new FormData(uploadForm);
        formData.delete('UploadFile');
        formData.set('Title', buildTitleForFile(baseTitle, file, totalFiles));
        formData.set('SummaryMode', summaryMode);
        formData.append('UploadFile', file, file.name);
        xhr.send(formData);
    });

    const startSequentialUpload = async () => {
        if (!uploadForm || !selectedFiles.length || isUploading) {
            return;
        }

        const titleInput = getTitleInput();
        const summaryModeInput = getSummaryModeInput();
        const baseTitle = (titleInput && titleInput.value ? titleInput.value : '').trim();
        const summaryMode = summaryModeInput ? summaryModeInput.value : 'detailed';

        if (!baseTitle) {
            setUploadStatus('Please enter a lecture title before uploading.', 'warning');
            return;
        }

        isUploading = true;
        uploadSubmitButton.disabled = true;
        renderFileProgressRows();
        setProgress(0, `0 / ${selectedFiles.length} files`);
        setUploadStatus(`Uploading ${selectedFiles.length} file(s)...`, 'info');

        let successCount = 0;
        let failureCount = 0;

        for (let index = 0; index < selectedFiles.length; index += 1) {
            const file = selectedFiles[index];
            setFileProgress(index, 0, 'Uploading', 'text-info');
            try {
                await uploadSingleFile(file, index, selectedFiles.length, baseTitle, summaryMode);
                successCount += 1;
            } catch (error) {
                failureCount += 1;
                const message = error && error.message ? error.message : 'Upload failed.';
                setFileProgress(index, 100, 'Failed', 'text-danger');
                setUploadStatus(message, 'danger');
            }

            setProgress(((index + 1) / selectedFiles.length) * 100, `${index + 1} / ${selectedFiles.length} files`);
        }

        isUploading = false;
        uploadSubmitButton.disabled = false;

        if (successCount > 0) {
            selectedFiles = [];
            syncUploadInput();
            renderSelectedFiles();
            renderFileProgressRows();
            setUploadStatus(`Uploaded ${successCount} file(s) successfully.${failureCount > 0 ? ` ${failureCount} file(s) failed.` : ''} AI processing is now running in the queue.`, failureCount > 0 ? 'warning' : 'success');
            return;
        }

        if (failureCount > 0) {
            setUploadStatus('No files were uploaded. Please fix the highlighted issues and try again.', 'danger');
        }
    };

    const addFiles = (incomingFiles) => {
        const combined = selectedFiles.concat(Array.from(incomingFiles || []));
        const deduped = [];
        const seen = new Set();

        combined.forEach((file) => {
            const signature = `${file.name}:${file.size}:${file.lastModified}`;
            if (!seen.has(signature)) {
                seen.add(signature);
                deduped.push(file);
            }
        });

        setFiles(deduped);
    };

    if (uploadForm) {
        const input = getUploadInput();
        if (uploadDropzone && input) {
            uploadDropzone.addEventListener('click', (event) => {
                if (event.target.closest('.upload-file-remove')) {
                    return;
                }
                input.click();
            });

            uploadDropzone.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    input.click();
                }
            });

            uploadDropzone.addEventListener('dragover', (event) => {
                event.preventDefault();
                uploadDropzone.classList.add('is-dragover');
            });

            uploadDropzone.addEventListener('dragleave', () => {
                uploadDropzone.classList.remove('is-dragover');
            });

            uploadDropzone.addEventListener('drop', (event) => {
                event.preventDefault();
                uploadDropzone.classList.remove('is-dragover');
                addFiles(event.dataTransfer.files);
            });

            input.addEventListener('change', () => {
                addFiles(input.files);
            });
        }

        if (uploadSelectedFiles) {
            uploadSelectedFiles.addEventListener('click', (event) => {
                const removeButton = event.target.closest('.upload-file-remove');
                if (!removeButton) {
                    return;
                }

                const index = Number(removeButton.dataset.fileIndex);
                if (Number.isNaN(index)) {
                    return;
                }

                selectedFiles = selectedFiles.filter((_, fileIndex) => fileIndex !== index);
                syncUploadInput();
                renderSelectedFiles();
            });
        }

        renderSelectedFiles();

        uploadForm.addEventListener('submit', async (event) => {
            event.preventDefault();

            if (isUploading) {
                return;
            }

            hideUploadStatus();
            if (!selectedFiles.length) {
                setUploadStatus('Select at least one file before uploading.', 'warning');
                return;
            }

            await startSequentialUpload();
        });
    }

    const syncSummaryPanelHeight = () => {
        if (!summaryCard) {
            return;
        }

        const isDesktop = window.matchMedia('(min-width: 992px)').matches;
        if (!isDesktop) {
            summaryCard.style.height = '';
            summaryCard.style.minHeight = '';
            return;
        }

        if (classroomAnchor) {
            const summaryTop = summaryCard.getBoundingClientRect().top;
            const classroomBottom = classroomAnchor.getBoundingClientRect().bottom;
            const targetHeight = Math.floor(classroomBottom - summaryTop);
            if (targetHeight > 0) {
                summaryCard.style.height = `${targetHeight}px`;
                summaryCard.style.minHeight = `${targetHeight}px`;
                return;
            }
        }

        if (leftStack) {
            const leftHeight = Math.ceil(leftStack.getBoundingClientRect().height);
            if (leftHeight > 0) {
                summaryCard.style.height = `${leftHeight}px`;
                summaryCard.style.minHeight = `${leftHeight}px`;
            }
        }
    };

    const refreshQueues = async () => {
        if (!statusUrl || Number.isNaN(pendingCount) || Number.isNaN(summaryCount) || Number.isNaN(archivedCount)) {
            return;
        }
        try {
            const response = await fetch(statusUrl, {
                method: 'GET',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                },
            });

            if (!response.ok) {
                return;
            }

            const data = await response.json();
            const nextPending = Number(data.pending_count);
            const nextSummary = Number(data.summary_count);
            const nextArchived = Number(data.archived_count);

            if (Number.isNaN(nextPending) || Number.isNaN(nextSummary) || Number.isNaN(nextArchived)) {
                return;
            }

            if (nextPending !== pendingCount || nextSummary !== summaryCount || nextArchived !== archivedCount) {
                if (typeof data.summaries_html === 'string') {
                    const queue = document.getElementById('summary-queue');
                    if (queue) {
                        queue.innerHTML = data.summaries_html;
                    }
                }

                if (typeof data.archived_summaries_html === 'string') {
                    const archivedQueue = document.getElementById('archived-summary-queue');
                    if (archivedQueue) {
                        archivedQueue.innerHTML = data.archived_summaries_html;
                    }
                }

                pendingCount = nextPending;
                summaryCount = nextSummary;
                archivedCount = nextArchived;
            }

            renderProcessingStatus(nextPending);
            syncSummaryPanelHeight();
        } catch (err) {
            // Ignore transient fetch errors and retry on next interval.
        }
    };

    renderProcessingStatus(pendingCount);
    syncSummaryPanelHeight();
    window.requestAnimationFrame(syncSummaryPanelHeight);
    window.setTimeout(syncSummaryPanelHeight, 120);

    if (leftStack && 'ResizeObserver' in window) {
        const observer = new ResizeObserver(syncSummaryPanelHeight);
        observer.observe(leftStack);
        if (classroomAnchor) {
            observer.observe(classroomAnchor);
        }
    }

    window.addEventListener('resize', syncSummaryPanelHeight);
    if (statusUrl) {
        setInterval(refreshQueues, 3000);
    }
});
