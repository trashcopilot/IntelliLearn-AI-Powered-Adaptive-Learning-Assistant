document.addEventListener('DOMContentLoaded', function () {
    const config = document.getElementById('ai-dashboard-config');
    const statusBanner = document.getElementById('ai-processing-status');

    if (!config || !statusBanner) {
        return;
    }

    let pendingCount = Number(config.dataset.pendingCount || 0);
    let summaryCount = Number(config.dataset.summaryCount || 0);
    let archivedCount = Number(config.dataset.archivedCount || 0);
    const statusUrl = config.dataset.statusUrl || '';

    if (!statusUrl || Number.isNaN(pendingCount) || Number.isNaN(summaryCount) || Number.isNaN(archivedCount)) {
        return;
    }

    const renderProcessingStatus = (count) => {
        if (count > 0) {
            const noun = count === 1 ? 'lecture' : 'lectures';
            statusBanner.textContent = `AI is processing ${count} ${noun}... this section updates automatically.`;
            statusBanner.classList.remove('d-none');
            return;
        }

        statusBanner.classList.add('d-none');
    };

    const refreshQueues = async () => {
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
        } catch (err) {
            // Ignore transient fetch errors and retry on next interval.
        }
    };

    renderProcessingStatus(pendingCount);
    setInterval(refreshQueues, 3000);
});
