document.addEventListener('DOMContentLoaded', function () {
    const canvas = document.getElementById('progressChart');
    if (!canvas) return;

    const rows = Array.from(document.querySelectorAll('table tbody tr'));
    const labels = [];
    const data = [];

    rows.forEach((row) => {
        const cells = row.querySelectorAll('td');
        if (cells.length >= 3) {
            const concept = cells[0].innerText.trim();
            labels.push(concept.length > 15 ? concept.substring(0, 12) + '...' : concept);
            data.push(parseFloat((cells[1].innerText || '0').replace('%', '')) || 0);
        }
    });

    // Create gradient for the fill
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
    gradient.addColorStop(0, 'rgba(104, 215, 255, 0.35)');
    gradient.addColorStop(0.5, 'rgba(104, 215, 255, 0.15)');
    gradient.addColorStop(1, 'rgba(104, 215, 255, 0)');

    new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Score %',
                data,
                borderColor: '#68d7ff',
                backgroundColor: gradient,
                borderWidth: 3,
                pointRadius: 6,
                pointHoverRadius: 8,
                pointBackgroundColor: '#68d7ff',
                pointBorderColor: '#ffffff',
                pointBorderWidth: 2.5,
                pointHoverBackgroundColor: '#ffffff',
                pointHoverBorderColor: '#68d7ff',
                pointHoverBorderWidth: 3,
                fill: true,
                tension: 0.45,
                segment: {
                    borderColor: ctx => {
                        const value = ctx.p1DataIndex >= 0 ? data[ctx.p1DataIndex] : 0;
                        if (value >= 80) return '#68d7ff';
                        if (value >= 60) return '#a78bfa';
                        return '#f472b6';
                    },
                },
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                filler: {
                    propagate: true,
                },
                legend: {
                    display: true,
                    position: 'top',
                    labels: {
                        color: 'rgba(234, 242, 255, 0.95)',
                        font: { size: 13, weight: '600' },
                        padding: 20,
                        usePointStyle: true,
                        pointStyle: 'circle',
                    },
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
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            if (context.parsed.y !== null) {
                                label += context.parsed.y + '%';
                            }
                            return label;
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
                        color: 'rgba(104, 215, 255, 0.1)',
                        lineWidth: 1.5,
                        drawBorder: false,
                    },
                },
                x: {
                    ticks: {
                        color: 'rgba(234, 242, 255, 0.6)',
                        font: { size: 11, weight: '500' },
                    },
                    grid: {
                        color: 'rgba(104, 215, 255, 0.05)',
                        lineWidth: 0.5,
                        drawBorder: false,
                    },
                },
            },
        },
    });
});
