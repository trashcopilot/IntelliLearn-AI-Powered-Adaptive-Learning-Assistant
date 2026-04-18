document.addEventListener('DOMContentLoaded', function () {
    const config = document.getElementById('ai-dashboard-config');
    const statusBanner = document.getElementById('ai-processing-status');
    const leftStack = document.getElementById('educator-left-stack');
    const classroomAnchor = document.getElementById('classroom-card-anchor');
    const summaryCard = document.querySelector('.queue-card-scroll');

    let pendingCount = 0;
    let summaryCount = 0;
    let archivedCount = 0;
    let statusUrl = '';

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
