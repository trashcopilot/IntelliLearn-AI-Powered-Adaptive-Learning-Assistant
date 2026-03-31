document.addEventListener('DOMContentLoaded', function () {
    const canvas = document.getElementById('progressChart');
    if (!canvas) return;

    const rows = Array.from(document.querySelectorAll('table tbody tr'));
    const labels = [];
    const data = [];

    rows.forEach((row) => {
        const cells = row.querySelectorAll('td');
        if (cells.length >= 3) {
            labels.push(cells[0].innerText.trim());
            data.push(parseFloat((cells[1].innerText || '0').replace('%', '')) || 0);
        }
    });

    new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Score %',
                data,
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37, 99, 235, 0.15)',
                fill: true,
                tension: 0.3,
            }],
        },
        options: {
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                },
            },
        },
    });
});
