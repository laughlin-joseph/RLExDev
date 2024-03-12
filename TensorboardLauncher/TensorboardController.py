import os
import subprocess
from kivy.app import App
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.button import Button
from kivy.lang import Builder
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.filechooser import FileChooserIconView
from kivy.uix.widget import Widget

class LoadDialog(FloatLayout):
    load = ObjectProperty(None)
    cancel = ObjectProperty(None)

class LauncherGUI(BoxLayout):
    def __init__(self, **kwargs):
        super(LauncherGUI, self).__init__(**kwargs)

class TensorboardApp(App):
    def __init__(self, **kwargs):
        super(TensorboardApp, self).__init__(**kwargs)
        self.tensorboard_process = None

    def build(self):
        return LauncherGUI()

    def directory_selected(self, instance):
        self.file_path_input.text = instance.selection[0]
    
    def dismiss_popup(self):
        self._popup.dismiss()

    def load(self, path, filename):
        with open(os.path.join(path, filename[0])) as stream:
            self.text_input.text = stream.read()

    def select_directory(self, instance):
        content = LoadDialog(load=self.load, cancel=self.dismiss_popup)
        self._popup = Popup(title="Load file", content=content, size_hint=(0.9, 0.9))
        self._popup.open()

    def show_message(self, message):
        popup = BoxLayout(orientation='vertical', padding=10, spacing=10)
        popup.add_widget(Label(text=message))
        popup.add_widget(Button(text='Close', on_press=lambda *args: self.dismiss_popup()))
        self._popup = popup
        self._popup.open()

    def toggle_tensorboard(self, instance):
        directory_path = self.file_path_input.text
        if not os.path.exists(directory_path):
            self.show_message('Error: Directory does not exist!')
            return

        if self.tensorboard_process is None:
            # Start TensorBoard
            command = ['tensorboard', '--logdir', directory_path]
            self.tensorboard_process = subprocess.Popen(command)
            self.tensorboard_button.text = 'Stop TensorBoard'
            self.show_message('TensorBoard started.')
        else:
            # Stop TensorBoard
            self.tensorboard_process.terminate()
            self.tensorboard_process = None
            self.tensorboard_button.text = 'Start TensorBoard'
            self.show_message('TensorBoard stopped.')
        
if __name__ == '__main__':
    TensorboardApp().run()