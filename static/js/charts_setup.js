document.addEventListener('DOMContentLoaded', function () {
    const canvas = document.getElementById('progressChart');
    if (!canvas) return;

    const points = Array.from(document.querySelectorAll('#attempt-chart-data .attempt-chart-point'));
    const labels = [];
    const data = [];
    const concepts = [];

    points.forEach((point) => {
        if (point.dataset.empty === '1') {
            return;
        }

        const label = (point.dataset.label || '').trim();
        const concept = (point.dataset.concept || '').trim();
        const parsedScore = parseFloat((point.dataset.score || '').trim());

        if (!label || Number.isNaN(parsedScore)) {
            return;
        }

        labels.push(label);
        concepts.push(concept);
        data.push(parsedScore);
    });

    if (!data.length) {
        const fallback = document.createElement('div');
        fallback.className = 'attempts-empty-state';
        fallback.textContent = 'No attempts yet.';
        canvas.replaceWith(fallback);
        return;
    }

    const chronological = labels.map((label, index) => ({
        label,
        concept: concepts[index],
        score: data[index],
    })).reverse();

    const orderedLabels = chronological.map((item) => item.label);
    const orderedScores = chronological.map((item) => item.score);
    const orderedConcepts = chronological.map((item) => item.concept);

    // Create gradient for the fill
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
    gradient.addColorStop(0, 'rgba(104, 215, 255, 0.42)');
    gradient.addColorStop(0.45, 'rgba(104, 215, 255, 0.18)');
    gradient.addColorStop(1, 'rgba(104, 215, 255, 0)');

    new Chart(canvas, {
        type: 'line',
        data: {
            labels: orderedLabels,
            datasets: [{
                label: 'Score %',
                data: orderedScores,
                borderColor: '#68d7ff',
                backgroundColor: gradient,
                borderWidth: 3.5,
                pointRadius: 4,
                pointHoverRadius: 7,
                pointBackgroundColor: '#68d7ff',
                pointBorderColor: '#f8fbff',
                pointBorderWidth: 2,
                pointHoverBackgroundColor: '#ffffff',
                pointHoverBorderColor: '#68d7ff',
                pointHoverBorderWidth: 3,
                fill: true,
                tension: 0.35,
                segment: {
                    borderColor: ctx => {
                        const value = ctx.p1DataIndex >= 0 ? orderedScores[ctx.p1DataIndex] : 0;
                        if (value >= 80) return '#68d7ff';
                        if (value >= 60) return '#a78bfa';
                        return '#f472b6';
                    },
                },
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                filler: {
                    propagate: true,
                },
                legend: {
                    display: false,
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleColor: '#68d7ff',
                    bodyColor: 'rgba(234, 242, 255, 0.9)',
                    borderColor: '#68d7ff',
                    borderWidth: 1.5,
                    padding: 12,
                    titleFont: { size: 12, weight: '600' },
                    bodyFont: { size: 11 },
                    callbacks: {
                        label: function(context) {
                            const concept = orderedConcepts[context.dataIndex] || 'Attempt';
                            const score = context.parsed.y !== null ? `${context.parsed.y}%` : '';
                            return `${concept}: ${score}`;
                        }
                    }
                },
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        color: 'rgba(234, 242, 255, 0.6)',
                        font: { size: 11, weight: '500' },
                        stepSize: 25,
                        callback: function(value) {
                            return value + '%';
                        }
                    },
                    grid: {
                        color: 'rgba(104, 215, 255, 0.08)',
                        lineWidth: 1,
                        drawBorder: false,
                    },
                },
                x: {
                    ticks: {
                        color: 'rgba(234, 242, 255, 0.6)',
                        font: { size: 11, weight: '500' },
                        maxRotation: 0,
                        autoSkip: true,
                        maxTicksLimit: 7,
                    },
                    grid: {
                        color: 'rgba(104, 215, 255, 0.04)',
                        lineWidth: 0.5,
                        drawBorder: false,
                    },
                },
            },
        },
    });
});
