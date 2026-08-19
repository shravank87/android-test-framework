from atf import Screen, by_desc, by_id, by_text


class SettingsScreen(Screen):
    SEARCH = by_id("com.android.settings:id/search_action_bar")
    SEARCH_INPUT = by_id("android:id/search_src_text")
    NAVIGATE_UP = by_desc("Navigate up")

    def title_visible(self, title):
        return self.is_present(by_text(title))

    def open_entry(self, title):
        self.scroll_to_text(title)
        return self.tap(by_text(title))

    def open_search(self):
        return self.tap(self.SEARCH)
