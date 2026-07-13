from app.arxiv import html_to_text


def test_html_to_text_excludes_executable_and_navigation_content():
    html = """
    <html><head><style>.secret { display:none }</style></head>
    <body><nav>Menu</nav><h1>Paper title</h1>
    <p>Visible <strong>finding</strong>.</p>
    <script>alert('should never be extracted')</script>
    <footer>Copyright</footer></body></html>
    """
    text = html_to_text(html)
    assert "Paper title" in text
    assert "Visible finding" in text
    assert "should never be extracted" not in text
    assert "display:none" not in text
    assert "Menu" not in text
    assert "Copyright" not in text


def test_html_to_text_caps_extracted_text():
    text = html_to_text("<p>word</p>" * 1_000_000)
    assert len(text) <= 2_000_000
