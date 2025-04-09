$(document).ready(function() {
    console.log("Document ready, initializing autocomplete...");

    // Initialize autocomplete for name fields
    $(".autocomplete").each(function() {
        const field = $(this).data("field"); 
        // e.g. "Nama Lengkap Peserta (Suami)" or "Nama Lengkap Peserta (Istri)"

        $(this).autocomplete({
            source: function(request, response) {
                $.ajax({
                    url: "/api/autocomplete",
                    dataType: "json",
                    data: {
                        term: request.term,
                        field: field
                    },
                    success: function(data) {
                        response(data.suggestions);
                    },
                    error: function() {
                        response([]);
                    }
                });
            },
            minLength: 1,
            select: function(event, ui) {
                // If user selects from the autocomplete list
                $(this).val(ui.item.value);
                // Force a detail fetch to auto-fill Tanggal Pernikahan if it exists
                fetchCompleteDetails();
                return false;  // ensures the selected value is put in the input
            }
        });
    });

    // Additional triggers: on "blur" or "change" in either suami or istri input
    $("#namaSuami, #namaIstri").on("blur", function() {
        fetchCompleteDetails();
    });

    // Function to fetch the person details from the server
    function fetchCompleteDetails() {
        const suamiVal = $("#namaSuami").val().trim();
        const istriVal = $("#namaIstri").val().trim();

        if (!suamiVal && !istriVal) {
            return;  // no point searching if both are empty
        }

        $.ajax({
            url: "/api/get_person_details",
            dataType: "json",
            data: {
                nama_suami: suamiVal,
                nama_istri: istriVal
            },
            success: function(data) {
                if (data.success) {
                    // Fill out fields
                    const p = data.person;
                    $("#namaSuami").val(p["Nama Lengkap Peserta (Suami)"]);
                    $("#namaIstri").val(p["Nama Lengkap Peserta (Istri)"]);
                    $("#phoneNumber").val(p["Nomor Handphone/ WA"]);

                    const existingDate = p["Tanggal Pernikahan"];
                    if (existingDate) {
                        $("#tanggalPernikahan").val(formatDateForInput(existingDate));
                    }
                }
            },
            error: function(xhr, status, error) {
                console.error("Error fetching person details:", error);
            }
        });
    }

    // Helper to convert whatever date format we have in the Excel to YYYY-MM-DD
    function formatDateForInput(dateStr) {
        try {
            const d = new Date(dateStr);
            return d.toISOString().split("T")[0];
        } catch (e) {
            return "";
        }
    }

    // Handle form submission
    $("#presenceForm").submit(function(e) {
        e.preventDefault();

        const formData = new FormData(this);

        $.ajax({
            url: "/api/submit",
            type: "POST",
            data: formData,
            processData: false,
            contentType: false,
            success: function(response) {
                if (response.success) {
                    // Show success modal
                    $("#successModal").modal("show");
                    // Reset form after short delay
                    setTimeout(() => {
                        $("#presenceForm")[0].reset();
                    }, 1000);
                } else {
                    // Show error modal
                    $("#errorMessage").text(response.message || "An unknown error occurred.");
                    $("#errorModal").modal("show");
                }
            },
            error: function(xhr) {
                console.error("Submit error:", xhr);
                // Show error modal
                let errorMsg = "An error occurred while processing your request.";
                try {
                    errorMsg = JSON.parse(xhr.responseText).detail;
                } catch (e) { /* ignore */ }
                $("#errorMessage").text(errorMsg);
                $("#errorModal").modal("show");
            }
        });
    });
});
